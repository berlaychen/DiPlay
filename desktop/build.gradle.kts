plugins {
    kotlin("jvm") version "2.2.20"
    application
}
kotlin { jvmToolchain(17) }
dependencies { implementation("org.bouncycastle:bcprov-jdk18on:1.80") }
application { mainClass.set("dev.diplay.desktop.MainKt") }
val prepareCore by tasks.registering(Exec::class) {
    commandLine("python3", "build.py", "prepare")
    inputs.dir("../shared/src/main/java")
    inputs.file("build.py")
    outputs.dir(layout.buildDirectory.dir("generated"))
}
kotlin.sourceSets.main { kotlin.srcDir(layout.buildDirectory.dir("generated")) }
tasks.compileKotlin { dependsOn(prepareCore) }
tasks.register<JavaExec>("coreTest") {
    dependsOn(tasks.classes)
    classpath = sourceSets.main.get().runtimeClasspath
    mainClass.set("dev.diplay.desktop.MainKt")
    args("--self-test")
}
tasks.register<Jar>("fatJar") {
    dependsOn(tasks.classes)
    archiveFileName.set("diplay-core.jar")
    destinationDirectory.set(layout.buildDirectory)
    duplicatesStrategy = DuplicatesStrategy.EXCLUDE
    manifest { attributes("Main-Class" to "dev.diplay.desktop.MainKt") }
    from(sourceSets.main.get().output)
    from(configurations.runtimeClasspath.get().map { if (it.isDirectory) it else zipTree(it) })
    exclude("META-INF/*.SF", "META-INF/*.RSA", "META-INF/*.DSA")
}
