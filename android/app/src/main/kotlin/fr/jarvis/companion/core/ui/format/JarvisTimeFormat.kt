package fr.jarvis.companion.core.ui.format

import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/** Parsing et formatage tolérants des dates ISO renvoyées par le serveur. */
object JarvisTimeFormat {
    private val timeFormatter = DateTimeFormatter.ofPattern("HH:mm", Locale.FRANCE)
    private val dayFormatter = DateTimeFormatter.ofPattern("EEEE d MMMM", Locale.FRANCE)
    private val shortDayFormatter = DateTimeFormatter.ofPattern("EEE d", Locale.FRANCE)

    /** Parse un ISO local ou avec offset ; null si illisible. */
    fun parseIso(iso: String?): LocalDateTime? {
        if (iso.isNullOrBlank()) return null
        return try {
            LocalDateTime.parse(iso.take(19))
        } catch (_: Exception) {
            try {
                OffsetDateTime.parse(iso).atZoneSameInstant(ZoneId.systemDefault()).toLocalDateTime()
            } catch (_: Exception) {
                try {
                    LocalDate.parse(iso.take(10)).atStartOfDay()
                } catch (_: Exception) {
                    null
                }
            }
        }
    }

    /** « 14:05 » ou la chaîne brute si non parsable. */
    fun timeOrRaw(iso: String?): String {
        val parsed = parseIso(iso) ?: return iso.orEmpty()
        return parsed.format(timeFormatter)
    }

    /** « mercredi 16 juillet ». */
    fun dayLabel(date: LocalDate): String = date.format(dayFormatter)

    /** « mer. 16 ». */
    fun shortDayLabel(date: LocalDate): String = date.format(shortDayFormatter)

    /** « à l'instant », « il y a 12 min », « il y a 3 h », « il y a 2 j », « jamais ». */
    fun relativeFromNow(epochMillis: Long?): String {
        if (epochMillis == null) return "jamais"
        val minutes = (System.currentTimeMillis() - epochMillis) / 60_000
        return when {
            minutes < 1 -> "à l'instant"
            minutes < 60 -> "il y a $minutes min"
            minutes < 60 * 24 -> "il y a ${minutes / 60} h"
            else -> "il y a ${minutes / (60 * 24)} j"
        }
    }

    /** Échéance relative d'une date « YYYY-MM-DD » : « aujourd'hui », « demain », « en retard (3 j) », « dans 5 j ». */
    fun dueLabel(dueDate: String?): String? {
        if (dueDate.isNullOrBlank()) return null
        val due = try {
            LocalDate.parse(dueDate.take(10))
        } catch (_: Exception) {
            return dueDate
        }
        val today = LocalDate.now()
        val days = java.time.temporal.ChronoUnit.DAYS.between(today, due)
        return when {
            days < 0 -> "en retard (${-days} j)"
            days == 0L -> "aujourd'hui"
            days == 1L -> "demain"
            else -> "dans $days j"
        }
    }

    /** Salutation selon l'heure — voix majordome. */
    fun greeting(now: LocalDateTime = LocalDateTime.now()): String = when (now.hour) {
        in 5..11 -> "Bonjour, Monsieur."
        in 12..17 -> "Bon après-midi, Monsieur."
        else -> "Bonsoir, Monsieur."
    }

    /** Instant epoch → LocalDate locale. */
    fun toLocalDate(epochMillis: Long): LocalDate =
        Instant.ofEpochMilli(epochMillis).atZone(ZoneId.systemDefault()).toLocalDate()
}
