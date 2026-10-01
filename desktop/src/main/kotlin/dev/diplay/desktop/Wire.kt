package dev.diplay.desktop

import com.shilapi.xcertplay.airplay.BplistCodec
import com.shilapi.xcertplay.transport.BlockingDuplexByteStream
import java.io.*
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/** A bounded, length-prefixed binary-plist pipe. stdout is NEVER a log stream. */
class Wire(input: InputStream, output: OutputStream) {
    private val input = DataInputStream(BufferedInputStream(input))
    private val output = DataOutputStream(BufferedOutputStream(output))
    @Synchronized fun send(message: Map<String, Any?>) {
        val bytes = BplistCodec.encode(message)
        require(bytes.size in 1..MAX_BYTES)
        output.writeInt(bytes.size)
        output.write(bytes)
        output.flush()
    }
    fun receive(): Map<String, Any?>? {
        val size = try { input.readInt() } catch (_: EOFException) { return null }
        require(size in 1..MAX_BYTES) { "Invalid IPC frame size" }
        val bytes = ByteArray(size)
        input.readFully(bytes)
        val decoded = BplistCodec.decode(bytes)
        require(decoded is Map<*, *> && decoded.keys.all { it is String }) { "Invalid IPC object" }
        @Suppress("UNCHECKED_CAST")
        return decoded as Map<String, Any?>
    }
    companion object { const val MAX_BYTES = 4 * 1024 * 1024 }
}

/** RFCOMM is owned by BlueZ/Python. The JVM sees exactly the upstream byte-stream seam. */
class PipeDuplex(private val wire: Wire, private val outputEvent: String = "bt_send") : BlockingDuplexByteStream {
    init { require(outputEvent == "bt_send" || outputEvent == "usb_send") }
    private val chunks = ArrayBlockingQueue<ByteArray>(64)
    private val closed = AtomicBoolean(false)
    private var pending = ByteArray(0)
    private var offset = 0
    fun offer(data: ByteArray) {
        require(data.size <= 16384)
        if (closed.get()) return
        if (!chunks.offer(data.copyOf())) { close(); throw IOException("RFCOMM input overflow") }
    }
    override fun send(data: ByteArray) {
        if (closed.get()) throw EOFException("RFCOMM closed")
        wire.send(mapOf("event" to outputEvent, "data" to data))
    }
    @Synchronized override fun recv(maxBytes: Int, timeoutMillis: Long): ByteArray? {
        require(maxBytes > 0 && timeoutMillis >= 0)
        if (offset >= pending.size) {
            if (closed.get() && chunks.isEmpty()) return ByteArray(0)
            pending = chunks.poll(timeoutMillis, TimeUnit.MILLISECONDS) ?: return null
            offset = 0
        }
        val count = minOf(maxBytes, pending.size - offset)
        val bytes = pending.copyOfRange(offset, offset + count)
        offset += count
        return bytes
    }
    override fun close() {
        if (closed.compareAndSet(false, true)) {
            chunks.clear(); chunks.offer(ByteArray(0))
        }
    }
}

/** Protocol payloads and identifiers in upstream log strings are intentionally NOT exported. */
@Suppress("UNUSED_PARAMETER")
object DesktopLog {
    fun d(tag: String, message: String) = 0
    fun v(tag: String, message: String) = 0
    fun i(tag: String, message: String) = 0
    fun w(tag: String, message: String, error: Throwable? = null): Int {
        System.err.println("$tag: warning${error?.let { " (${it.javaClass.simpleName})" } ?: ""}"); return 0
    }
    fun e(tag: String, message: String, error: Throwable? = null): Int {
        System.err.println("$tag: error${error?.let { " (${it.javaClass.simpleName})" } ?: ""}"); return 0
    }
}
