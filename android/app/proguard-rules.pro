# JARVIS Companion — règles R8 release
-keepattributes SourceFile,LineNumberTable
-keep class fr.jarvis.companion.BuildConfig { *; }

# Firebase Messaging (réflexion)
-keep class com.google.firebase.** { *; }
-dontwarn com.google.firebase.**

# Porcupine native
-keep class ai.picovoice.** { *; }
-dontwarn ai.picovoice.**
