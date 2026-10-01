package com.shilapi.xcertplay.network

import java.net.Socket
import jdk.net.ExtendedSocketOptions

/** Desktop adapter for the only Android-specific helper referenced by AirPlaySession. */
internal object TcpLiveness {
    fun configure(socket: Socket, diagnostic: (String) -> Unit) {
        socket.keepAlive = true
        socket.soTimeout = 0 // A static map is not a disconnected phone.
        runCatching {
            socket.setOption(ExtendedSocketOptions.TCP_KEEPIDLE, 10)
            socket.setOption(ExtendedSocketOptions.TCP_KEEPINTERVAL, 3)
            socket.setOption(ExtendedSocketOptions.TCP_KEEPCOUNT, 3)
        }.onFailure { diagnostic("TCP keepalive tuning unavailable") }
    }
}
