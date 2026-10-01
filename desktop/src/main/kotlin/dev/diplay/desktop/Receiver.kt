package dev.diplay.desktop

import com.shilapi.xcertplay.airplay.*
import com.shilapi.xcertplay.iap2.session.Iap2Session
import com.shilapi.xcertplay.media.MediaCodecSupport
import com.shilapi.xcertplay.mfi.*
import com.shilapi.xcertplay.transport.*
import java.io.*
import java.net.*
import java.nio.file.*
import java.nio.file.attribute.PosixFilePermissions
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

internal fun Map<String, Any?>.text(key: String, default: String = "") = this[key] as? String ?: default
internal fun Map<String, Any?>.number(key: String, default: Int) = (this[key] as? Number)?.toInt() ?: default
internal fun Map<String, Any?>.flag(key: String) = this[key] == true

/** Atomic, owner-only state. This is pairing state, NOT an Apple accessory identity. */
class StateStore(private val directory: Path) {
    init {
        Files.createDirectories(directory)
        Files.setPosixFilePermissions(directory, PosixFilePermissions.fromString("rwx------"))
    }
    fun read(name: String): Map<String, Any?>? {
        val path = directory.resolve(name)
        if (!Files.exists(path)) return null
        require(Files.size(path) <= Wire.MAX_BYTES) { "State file too large" }
        @Suppress("UNCHECKED_CAST")
        return BplistCodec.decode(Files.readAllBytes(path)) as? Map<String, Any?>
            ?: error("Invalid state file")
    }
    @Synchronized fun write(name: String, value: Map<String, Any?>) {
        val temp = Files.createTempFile(directory, ".$name", ".tmp")
        try {
            Files.setPosixFilePermissions(temp, PosixFilePermissions.fromString("rw-------"))
            Files.write(temp, BplistCodec.encode(value))
            Files.move(temp, directory.resolve(name), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
        } finally { Files.deleteIfExists(temp) }
    }
    fun identity(): AirPlayIdentity {
        read("receiver.plist")?.let {
            return AirPlayIdentity(it["private"] as ByteArray, it["public"] as ByteArray, it.text("id"))
        }
        return AirPlayIdentity.generate().also {
            write("receiver.plist", mapOf("private" to it.privateKey, "public" to it.publicKey, "id" to it.pairingId))
        }
    }
    fun pairings(): PairingStore {
        val values = ConcurrentHashMap<String, ByteArray>()
        read("pairings.plist")?.forEach { (id, key) -> values[id] = key as ByteArray }
        val store = PairingStore { id, key ->
            require(values.size < 32 || values.containsKey(id)) { "Pairing limit reached" }
            values[id] = key.copyOf(); write("pairings.plist", values)
        }
        values.toMap().forEach { (id, key) -> store.save(id, key) }
        return store
    }
}

/** Linux-neutral CarPlay control/media runtime. Platform operations live in the Python broker. */
class Receiver(private val wire: Wire, private val settings: Map<String, Any?>) : Closeable {
    private val stopped = AtomicBoolean(false)
    private val states = StateStore(Path.of(settings.text("state_dir")))
    private val identity = states.identity()
    private val pairings = states.pairings()
    private val width = settings.number("width", 1280).also { require(it in 320..1920 && it % 2 == 0) }
    private val height = settings.number("height", 720).also { require(it in 240..1200 && it % 2 == 0) }
    private val fps = settings.number("fps", 30).also { require(it in setOf(24, 25, 30, 60)) }
    private val mac = settings.text("bt_mac").also { require(Regex("[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}").matches(it)) }
    private val address = InetAddress.getByName(settings.text("address"))
    private val config = AirPlayConfig(
        deviceName = settings.text("name", "DiPlay Linux"), deviceId = mac, btMac = mac,
        sourceVersion = "950.7.1", main = AirPlayDisplayConfig(width, height,
            settings.number("width_mm", 200), settings.number("height_mm", 113), fps),
        port = settings.number("airplay_port", 7000).also { require(it in 1024..65535) },
        hevc = false, microphone = settings.flag("microphone"),
        manufacturer = "DiPlay", model = "DiPlay-Linux", oemLabel = "DiPlay",
    )
    private val authentication: MfiAuthenticator = when (settings.text("auth_mode")) {
        "local" -> {
            val dir = File(settings.text("auth_dir"))
            require(dir.isDirectory) { "Authentication not provisioned: auth.directory is missing" }
            LocalMfiAuthenticationClient.load(dir)
        }
        "remote" -> RemoteMfiAuthenticationClient(settings.text("auth_url"), settings.text("auth_token").ifEmpty { null })
        else -> error("Set auth.mode to local or remote; source builds do not contain an accessory identity")
    }
    private val endpoint = Iap2WirelessCarPlayEndpoint(
        settings.text("ssid"), settings.text("password"), settings.number("channel", 36),
        Iap2WirelessSecurity.WPA_WPA2, listOf(address.hostAddress!!.substringBefore('%')),
        config.port, config.deviceId, identity.publicKeyHex, config.sourceVersion,
    )
    private val identification = Iap2IdentificationConfig(
        config.deviceName, config.model, config.manufacturer, identity.pairingId, "0.1.0", "1",
        wireless = Iap2WirelessIdentification(mac, endpoint.ssid),
        externalAccessoryProtocol = "dev.diplay.desktop",
    )
    private val media = PipeMediaSink(wire)
    private val engine = CarPlayMediaEngine(media, microphoneEnabled = config.microphone)
    private val sessions = CopyOnWriteArrayList<AirPlaySession>()
    private val channels = CopyOnWriteArrayList<Iap2Session>()
    private var listener: ServerSocket? = null
    @Volatile private var active: AirPlaySession? = null
    @Volatile private var bt: PipeDuplex? = null
    private val handoff = AtomicBoolean(false)
    private val tunnelReady = AtomicBoolean(false)
    private val btGeneration = AtomicInteger(0)

    private fun state(value: String) = wire.send(mapOf("event" to "status", "state" to value))

    fun start() {
        authentication.readCertificate(65525) // Fail before touching Bluetooth, not halfway through pairing.
        engine.setIapTunnelHandler { stream ->
            if (stopped.get()) false else {
                val session = Iap2Session.openTunnel(stream)
                channels += session
                worker("iap2-wifi") {
                    runControl(session) { tunnelReady.set(true); finishHandoff() }
                }
                true
            }
        }
        val server = ServerSocket().apply {
            reuseAddress = true
            bind(InetSocketAddress(address, config.port))
        }
        listener = server
        worker("airplay-listener") {
            while (!stopped.get()) {
                val socket = server.accept().apply { tcpNoDelay = true; soTimeout = 60_000 }
                if (sessions.size >= 4) { socket.close(); continue }
                val session = AirPlaySession(socket, config, identity, pairings, authentication,
                    object : AirPlaySessionListener {
                        override fun onSessionActive(session: AirPlaySession) {
                            active?.takeIf { it !== session }?.close()
                            active = session
                            state("connected")
                        }
                        override fun onSessionEnded(session: AirPlaySession) {
                            sessions.remove(session)
                            if (active === session) {
                                active = null
                                channels.toList().forEach { runCatching { it.close() } }
                                channels.clear()
                                handoff.set(false); tunnelReady.set(false)
                                state("disconnected")
                            }
                        }
                        override fun onCommand(session: AirPlaySession, type: String, params: Map<String, Any?>) {
                            if (type.equals("disableBluetooth", true) || type.equals("disable-bluetooth", true)) {
                                handoff.set(true); finishHandoff()
                            }
                        }
                        override fun onTransportError(message: String) { state("transport_error") }
                        override fun onHostUiRequested(session: AirPlaySession) { state("home_requested") }
                    }, engine)
                sessions += session
                session.start()
            }
        }
        wire.send(mapOf("event" to "ready", "name" to config.deviceName, "port" to config.port,
            "txt" to mapOf("deviceid" to mac, "features" to "0x44540380,0x61", "flags" to "0x4",
                "model" to config.model, "srcvers" to config.sourceVersion, "protovers" to "1.1",
                "pi" to identity.pairingId, "pk" to identity.publicKeyHex)))
        state("waiting_for_phone")
    }

    private fun runControl(channel: Iap2Session, ready: () -> Unit = {}) {
        try {
            Iap2WirelessControlClient(channel, Iap2MfiAuthenticationClient(authentication)).run(
                identification, endpoint, timeoutMillis = Iap2WirelessControlClient.NO_TIMEOUT_MILLIS,
                onReady = { state("authenticated"); ready() },
            )
        } finally { channels.remove(channel); runCatching { channel.close() } }
    }
    private fun finishHandoff() {
        if (handoff.get() && tunnelReady.get()) {
            bt?.close(); bt = null
            wire.send(mapOf("event" to "bt_release"))
        }
    }
    fun command(value: Map<String, Any?>) {
        when (value.text("op")) {
            "bt_open" -> {
                if (active != null || bt != null) return
                val pipe = PipeDuplex(wire)
                bt = pipe
                val generation = btGeneration.incrementAndGet()
                val channel = Iap2Session.openWireless(pipe)
                channels += channel
                worker("iap2-bluetooth") {
                    try { runControl(channel) }
                    finally {
                        if (generation == btGeneration.get() && bt === pipe) {
                            bt = null
                            if (active == null) state("waiting_for_phone")
                        }
                    }
                }
            }
            "bt_data" -> bt?.offer(value["data"] as ByteArray)
            "bt_closed" -> { bt?.close(); bt = null }
            "touch" -> {
                @Suppress("UNCHECKED_CAST")
                val contacts = value["contacts"] as? List<Map<String, Any?>> ?: return
                require(contacts.size <= 2)
                active?.sendTouch(contacts.mapIndexed { slot, contact ->
                    val x = (contact["x"] as Number).toDouble()
                    val y = (contact["y"] as Number).toDouble()
                    require(x.isFinite() && y.isFinite())
                    AirPlayContact(slot, x.coerceIn(0.0, 1.0), y.coerceIn(0.0, 1.0), contact.flag("down"))
                })
            }
            "key" -> active?.let { session -> when (value.text("key")) {
                "home" -> session.sendKnob(AirPlayKnobState(home = true))
                "back" -> session.sendKnob(AirPlayKnobState(back = true))
                "select" -> session.sendKnob(AirPlayKnobState(select = true))
                "left" -> session.sendKnob(AirPlayKnobState(x = -1))
                "right" -> session.sendKnob(AirPlayKnobState(x = 1))
                "up" -> session.sendKnob(AirPlayKnobState(y = -1))
                "down" -> session.sendKnob(AirPlayKnobState(y = 1))
                "siri" -> session.invokeSiri()
                else -> throw IllegalArgumentException("Unknown key")
            } }
            "keyframe" -> active?.sendCommand(mapOf("type" to "forceKeyFrame"))
            "rendered" -> media.rendered()
            "mic_packet" -> media.microphonePacket(value.number("id", -1), value["data"] as ByteArray)
            "disconnect" -> { sessions.toList().forEach { it.close() }; bt?.close(); bt = null }
            else -> throw IllegalArgumentException("Unknown IPC operation")
        }
    }
    private fun worker(name: String, action: () -> Unit) = Thread({
        try { action() } catch (e: Exception) {
            if (!stopped.get()) wire.send(mapOf("event" to "error", "component" to name,
                "message" to e.javaClass.simpleName))
        }
    }, name).apply { isDaemon = true; start() }

    override fun close() {
        if (!stopped.compareAndSet(false, true)) return
        btGeneration.incrementAndGet()
        engine.setIapTunnelHandler(null)
        runCatching { listener?.close() }
        bt?.close()
        channels.toList().forEach { runCatching { it.close() } }
        sessions.toList().forEach { runCatching { it.close() } }
        media.close()
    }
}

/** Encoded media crosses the pipe; decoding never takes place on a protocol receive thread. */
class PipeMediaSink(private val wire: Wire) : MediaSink, Closeable {
    private val ids = ConcurrentHashMap<AudioStreamId, Int>()
    private val nextId = AtomicInteger(1)
    private val microphones = ConcurrentHashMap<Int, Pair<MicrophoneConfig, MicrophoneCounters>>()
    private val socket = DatagramSocket()
    @Volatile private var reportRendered: ((String) -> Unit)? = null
    private val firstRendered = AtomicBoolean(false)
    private fun id(stream: AudioStreamId) = ids.computeIfAbsent(stream) { nextId.getAndIncrement() }
    override fun onVideoCodec(type: Int, codec: VideoCodec) {
        require(codec == VideoCodec.H264) { "This Linux preview negotiates H.264 only" }
    }
    override fun setVideoDiagnosticHandler(type: Int, handler: (String) -> Unit) { reportRendered = handler }
    fun rendered() { if (firstRendered.compareAndSet(false, true)) reportRendered?.invoke("first frame rendered") }
    override fun onVideoConfig(type: Int, codecData: ByteArray) {
        firstRendered.set(false)
        val (sps, pps) = MediaCodecSupport.avcParameterSets(codecData)
        require(sps.isNotEmpty() && pps.isNotEmpty()) { "Invalid AVC parameter sets" }
        wire.send(mapOf("event" to "video_config", "data" to codecData))
    }
    override fun onVideoFrame(type: Int, naluBytes: ByteArray) {
        wire.send(mapOf("event" to "video", "data" to naluBytes,
            "key" to MediaCodecSupport.isRandomAccess(naluBytes, VideoCodec.H264),
            "time_us" to System.nanoTime() / 1000))
    }
    override fun onScreenStreamActive(type: Int, active: Boolean) {
        if (!active) wire.send(mapOf("event" to "video_stop"))
    }
    override fun onAudioStarted(id: AudioStreamId, format: AudioFormat, firstSample: Int) {
        wire.send(mapOf("event" to "audio_start", "id" to id(id), "codec" to format.codec.name,
            "rate" to format.sampleRate, "channels" to format.channels, "role" to format.audioType))
    }
    override fun onAudioRtp(id: AudioStreamId, format: AudioFormat, rtp: ByteArray, sample: Int) {
        wire.send(mapOf("event" to "audio", "id" to id(id), "data" to rtp))
    }
    override fun onAudioStopped(id: AudioStreamId) {
        ids[id]?.let { wire.send(mapOf("event" to "audio_stop", "id" to it)) }
    }
    override fun onMicrophoneStarted(id: AudioStreamId, config: MicrophoneConfig) {
        val key = id(id)
        microphones[key] = config to MicrophoneCounters()
        wire.send(mapOf("event" to "mic_start", "id" to key, "codec" to config.codec.name,
            "rate" to config.sampleRate, "channels" to config.channels,
            "frame_bytes" to config.frameBytes, "bitrate" to (config.bitrate ?: 48000)))
    }
    override fun onMicrophoneStopped(id: AudioStreamId) {
        ids[id]?.let { microphones.remove(it); wire.send(mapOf("event" to "mic_stop", "id" to it)) }
    }
    @Synchronized fun microphonePacket(id: Int, data: ByteArray) {
        val (config, counters) = microphones[id] ?: return
        require(data.size in 1..16384)
        if (config.codec == AudioCodecKind.LPCM) require(data.size == config.frameBytes)
        val payload = if (config.codec == AudioCodecKind.LPCM) MicrophonePacketizer.toWirePcm(data) else data
        val packet = MicrophonePacketizer.sealPacket(config.key, config.payloadType, counters, payload, config.samplesPerPacket)
        socket.send(DatagramPacket(packet, packet.size, config.host, config.port))
    }
    override fun close() { microphones.clear(); socket.close() }
}
