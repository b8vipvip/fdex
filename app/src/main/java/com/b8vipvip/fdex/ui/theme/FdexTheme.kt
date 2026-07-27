package com.b8vipvip.fdex.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val LightColors = lightColorScheme(
    primary = Color(0xFF0F766E),
    secondary = Color(0xFF2563EB),
    tertiary = Color(0xFF7C3AED),
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFF5EEAD4),
    secondary = Color(0xFF93C5FD),
    tertiary = Color(0xFFC4B5FD),
)

@Composable
fun FdexTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = if (isSystemInDarkTheme()) DarkColors else LightColors,
        content = content,
    )
}
