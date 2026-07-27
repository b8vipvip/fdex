package com.b8vipvip.fdex.update

import android.app.DownloadManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.Settings
import android.widget.Toast
import androidx.core.content.FileProvider
import java.io.File

object ApkUpdater {
    private const val APK_MIME = "application/vnd.android.package-archive"

    fun downloadAndInstall(context: Context, release: ReleaseInfo) {
        val appContext = context.applicationContext
        val apkUrl = release.apkUrl
        if (apkUrl.isNullOrBlank()) {
            openReleasePage(appContext, release.htmlUrl)
            Toast.makeText(appContext, "该版本未附带 APK，已打开发布页面", Toast.LENGTH_LONG).show()
            return
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && !appContext.packageManager.canRequestPackageInstalls()) {
            val intent = Intent(
                Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                Uri.parse("package:${appContext.packageName}"),
            ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            appContext.startActivity(intent)
            Toast.makeText(appContext, "请先允许 FDEX 安装未知应用，然后再次点击立即更新", Toast.LENGTH_LONG).show()
            return
        }

        val safeVersion = release.normalizedVersion.replace(Regex("[^0-9A-Za-z._-]"), "-")
        val fileName = "fdex-$safeVersion.apk"
        val destination = File(
            appContext.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS),
            fileName,
        )
        destination.delete()

        val request = DownloadManager.Request(Uri.parse(apkUrl))
            .setTitle("FDEX ${release.normalizedVersion}")
            .setDescription("正在下载更新")
            .setMimeType(APK_MIME)
            .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            .setDestinationInExternalFilesDir(appContext, Environment.DIRECTORY_DOWNLOADS, fileName)
            .setAllowedOverMetered(true)
            .setAllowedOverRoaming(false)

        val manager = appContext.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
        val downloadId = manager.enqueue(request)
        Toast.makeText(appContext, "已开始下载更新", Toast.LENGTH_SHORT).show()

        val receiver = object : BroadcastReceiver() {
            override fun onReceive(receiverContext: Context, intent: Intent) {
                if (intent.action != DownloadManager.ACTION_DOWNLOAD_COMPLETE) return
                if (intent.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1L) != downloadId) return

                runCatching { receiverContext.unregisterReceiver(this) }
                val cursor = manager.query(DownloadManager.Query().setFilterById(downloadId))
                cursor.use {
                    if (!it.moveToFirst()) return
                    val status = it.getInt(it.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS))
                    if (status != DownloadManager.STATUS_SUCCESSFUL) {
                        Toast.makeText(receiverContext, "更新下载失败", Toast.LENGTH_LONG).show()
                        return
                    }
                }
                installApk(receiverContext, destination)
            }
        }

        val filter = IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            appContext.registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("DEPRECATION")
            appContext.registerReceiver(receiver, filter)
        }
    }

    private fun installApk(context: Context, file: File) {
        val uri = FileProvider.getUriForFile(
            context,
            "${context.packageName}.fileprovider",
            file,
        )
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, APK_MIME)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        context.startActivity(intent)
    }

    fun openReleasePage(context: Context, url: String) {
        if (url.isBlank()) return
        context.startActivity(
            Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        )
    }
}
