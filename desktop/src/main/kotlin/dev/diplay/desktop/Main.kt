package dev.diplay.desktop

fun main(args: Array<String>) {
    if (args.contentEquals(arrayOf("--self-test"))) { selfTest(); return }
    if (args.size == 2 && args[0] == "--check-identity") {
        try {
            val client = com.shilapi.xcertplay.mfi.LocalMfiAuthenticationClient.load(java.io.File(args[1]))
            check(client.signChallenge(ByteArray(32)).size == 64)
            println("PASS JVM local P-256 certificate/key loading and signing; iPhone trust NOT tested")
        } catch (error: Exception) {
            System.err.println("Identity check failed (${error.javaClass.simpleName}); no credential data logged")
            kotlin.system.exitProcess(2)
        }
        return
    }
    val wire = Wire(System.`in`, System.out)
    var receiver: Receiver? = null
    var failed = false
    try {
        val initial = wire.receive() ?: return
        require(initial.text("op") == "start")
        @Suppress("UNCHECKED_CAST")
        val settings = initial["settings"] as? Map<String, Any?> ?: error("Missing settings")
        receiver = Receiver(wire, settings)
        val current = receiver
        Runtime.getRuntime().addShutdownHook(Thread { current.close() })
        current.start()
        while (true) {
            val command = wire.receive() ?: break
            if (command.text("op") == "stop") break
            try { current.command(command) }
            catch (e: Exception) { wire.send(mapOf("event" to "error", "component" to "command", "message" to e.javaClass.simpleName)) }
        }
    } catch (e: Exception) {
        failed = true
        runCatching { wire.send(mapOf("event" to "fatal", "message" to (e.message ?: e.javaClass.simpleName))) }
        System.err.println("DiPlay core stopped (${e.javaClass.simpleName})")
    } finally { receiver?.close() }
    if (failed) kotlin.system.exitProcess(2)
}
