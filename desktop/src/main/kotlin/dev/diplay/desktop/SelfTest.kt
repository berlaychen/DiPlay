package dev.diplay.desktop

import com.shilapi.xcertplay.airplay.*
import com.shilapi.xcertplay.iap2.body.Iap2BodyBuilder
import com.shilapi.xcertplay.iap2.wire.*
import com.shilapi.xcertplay.media.MediaCodecSupport
import java.io.*
import java.nio.file.Files

/** Device-free protocol regression checks. They are not an iPhone interoperability test. */
fun selfTest() {
    var count = 0
    fun test(name: String, block: () -> Unit) { block(); println("PASS $name"); count++ }
    test("binary plist IPC") {
        val bytes = ByteArrayOutputStream()
        val obj = mapOf("op" to "test", "data" to byteArrayOf(1, 2, 3), "number" to 1280, "enabled" to true)
        Wire(ByteArrayInputStream(byteArrayOf()), bytes).send(obj)
        val read = Wire(ByteArrayInputStream(bytes.toByteArray()), ByteArrayOutputStream()).receive()!!
        check(read.text("op") == "test" && (read["data"] as ByteArray).contentEquals(byteArrayOf(1, 2, 3)))
        check(read.number("number", 0) == 1280 && read.flag("enabled"))
    }
    test("oversize IPC rejected") {
        val bytes = ByteArrayOutputStream(); DataOutputStream(bytes).writeInt(Wire.MAX_BYTES + 1)
        check(runCatching { Wire(ByteArrayInputStream(bytes.toByteArray()), ByteArrayOutputStream()).receive() }.isFailure)
    }
    test("truncated IPC rejected") {
        check(runCatching { Wire(ByteArrayInputStream(byteArrayOf(0, 0, 0, 8, 1)), ByteArrayOutputStream()).receive() }.isFailure)
    }
    test("RFCOMM fragmentation and clean EOF") {
        val pipe = PipeDuplex(Wire(ByteArrayInputStream(byteArrayOf()), ByteArrayOutputStream()))
        pipe.offer(byteArrayOf(1, 2, 3)); check(pipe.recv(2, 1)!!.contentEquals(byteArrayOf(1, 2)))
        check(pipe.recv(2, 1)!!.contentEquals(byteArrayOf(3)))
        check(pipe.recv(2, 0) == null); pipe.close(); check(pipe.recv(2, 1)!!.isEmpty())
    }
    test("AVC length-prefix conversion and IDR") {
        val nals = byteArrayOf(0, 0, 0, 2, 0x65, 0x11)
        val annex = MediaCodecSupport.toAnnexB(nals)
        check(annex.contentEquals(byteArrayOf(0, 0, 0, 1, 0x65, 0x11)))
        check(MediaCodecSupport.isRandomAccess(annex, VideoCodec.H264))
        check(MediaCodecSupport.toAnnexB(byteArrayOf(0, 0, 3, 0, 1)).isEmpty())
    }
    test("microphone endian conversion") {
        check(MicrophonePacketizer.toWirePcm(byteArrayOf(1, 2, 3, 4)).contentEquals(byteArrayOf(2, 1, 4, 3)))
    }
    test("microphone authenticated encryption") {
        val key = ByteArray(32) { it.toByte() }; val counters = MicrophoneCounters()
        val body = byteArrayOf(0, 1, 2, 3)
        val packet = MicrophonePacketizer.sealPacket(key, 100, counters, body, 2)
        val nonce = ByteArray(12)
        val plain = AirPlayCrypto.chachaOpen(key, nonce, packet.copyOfRange(12, packet.size - 8), packet.copyOfRange(4, 12))
        check(plain.contentEquals(body) && counters.sequence == 1 && counters.timestamp == 2 && counters.nonce == 1L)
    }
    test("pairing identity persists without accessory credentials") {
        val dir = Files.createTempDirectory("diplay-state-")
        try {
            val a = StateStore(dir).identity(); val b = StateStore(dir).identity()
            check(a.privateKey.contentEquals(b.privateKey) && a.pairingId == b.pairingId)
            val pairs = StateStore(dir).pairings(); pairs.save("test-phone", ByteArray(32) { 7 })
            check(Files.size(dir.resolve("pairings.plist")) > 32)
        } finally { dir.toFile().deleteRecursively() }
    }
    test("HID touch is bounded by report contract") {
        val report = AirPlayHid.touchReport(listOf(AirPlayContact(0, 32.0, 48.0, true)))
        check(report.size == 12 && report[1] == 1.toByte())
    }
    test("wired IPC is distinct from the Bluetooth pipe") {
        val bytes = ByteArrayOutputStream()
        val pipe = PipeDuplex(Wire(ByteArrayInputStream(byteArrayOf()), bytes), "usb_send")
        pipe.send(byteArrayOf(0x55, 0x66))
        val message = Wire(ByteArrayInputStream(bytes.toByteArray()), ByteArrayOutputStream()).receive()!!
        check(message.text("event") == "usb_send")
        check((message["data"] as ByteArray).contentEquals(byteArrayOf(0x55, 0x66)))
        pipe.close()
    }
    test("wired CarPlay request carries USB IPv6, not Wi-Fi credentials") {
        val endpoint = com.shilapi.xcertplay.transport.Iap2WiredCarPlayEndpoint(
            listOf("fe80::1234"), 7000, "public-pairing-key", "950.7.1", "02:00:00:00:00:01")
        val frame = com.shilapi.xcertplay.transport.Iap2WiredControlClient.carPlayStartSession(endpoint)
        check(frame.messageId == 0x4301)
        val values = com.shilapi.xcertplay.iap2.body.Iap2BodyReader.of(frame).list()
        check(values.any { it.id == 0 } && values.none { it.id == 1 })
        val addresses = Iap2ParameterList.parse(values.single { it.id == 0 }.payload).asList()
        check(addresses.single().payload.contentEquals("fe80::1234\u0000".toByteArray()))
    }
    println("$count core checks passed; no physical CarPlay session was exercised")
}
