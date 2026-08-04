# JARVIS Companion — règles R8 release
-keepattributes SourceFile,LineNumberTable
-keepattributes Signature,InnerClasses,EnclosingMethod
-keepattributes RuntimeVisibleAnnotations,RuntimeVisibleParameterAnnotations,AnnotationDefault
-keep class fr.jarvis.companion.BuildConfig { *; }

# Retrofit lit les annotations et signatures génériques de cette interface.
-keep interface fr.jarvis.companion.network.JarvisApiService { *; }

# Gson sérialise ces DTO par réflexion. Les requêtes sans @SerializedName
# utilisent volontairement leurs noms snake_case comme contrat HTTP.
-keep class fr.jarvis.companion.network.**Request { *; }
-keep class fr.jarvis.companion.network.**Response { *; }
-keep class fr.jarvis.companion.network.LocationBatchPoint { *; }
-keep class fr.jarvis.companion.network.LocationBatchRejected { *; }
-keep class fr.jarvis.companion.voice.VoiceTurnResponse { *; }
-keepclassmembers,allowoptimization class * {
    @com.google.gson.annotations.SerializedName <fields>;
}

# Firebase Messaging (réflexion)
-keep class com.google.firebase.** { *; }
-dontwarn com.google.firebase.**

# Porcupine native
-keep class ai.picovoice.** { *; }
-dontwarn ai.picovoice.**

# Room entities and DAOs
-keep class fr.jarvis.companion.core.database.** { *; }
-keep @androidx.room.Entity class * { *; }
-keep @androidx.room.Dao interface * { *; }
