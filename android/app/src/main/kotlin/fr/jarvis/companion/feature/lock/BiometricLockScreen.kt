package fr.jarvis.companion.feature.lock

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Fingerprint
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import fr.jarvis.companion.core.ui.components.JarvisPrimaryButton
import fr.jarvis.companion.ui.theme.JarvisColors

@Composable
fun BiometricLockScreen(
    message: String?,
    onAuthenticate: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Icon(
            imageVector = Icons.Rounded.Fingerprint,
            contentDescription = null,
            tint = JarvisColors.Cyan,
        )
        Text(
            text = "JARVIS est verrouillé",
            style = MaterialTheme.typography.headlineSmall,
            modifier = Modifier.padding(top = 20.dp),
        )
        Text(
            text = message ?: "Confirmez votre identité pour accéder aux données privées.",
            style = MaterialTheme.typography.bodyMedium,
            textAlign = TextAlign.Center,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 8.dp, bottom = 24.dp),
        )
        JarvisPrimaryButton(
            text = "Déverrouiller",
            onClick = onAuthenticate,
            icon = Icons.Rounded.Fingerprint,
            modifier = Modifier.fillMaxWidth(),
        )
    }
}
