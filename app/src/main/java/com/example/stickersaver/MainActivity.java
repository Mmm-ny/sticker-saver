package com.example.stickersaver;

import android.Manifest;
import android.app.Activity;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Movie;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.MediaStore;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private static final String DEFAULT_SERVER_BASE_URL = "http://10.0.2.2:8080";
    private static final int STORAGE_PERMISSION_REQUEST = 42;
    private static final int PICK_IMAGE_REQUEST = 43;

    private final ExecutorService executor = Executors.newFixedThreadPool(4);
    private LinearLayout resultsList;
    private LinearLayout recentList;
    private TextView statusText;
    private EditText searchInput;
    private EditText serverInput;
    private ProgressBar progressBar;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        buildUi();
        ensureLegacyStoragePermission();
        loadRecentSaves();
        handleIncomingIntent(getIntent());
        search("哈哈", true);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleIncomingIntent(intent);
    }

    private void buildUi() {
        ScrollView scrollView = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(16), dp(18), dp(16), dp(24));
        root.setBackgroundColor(Color.rgb(250, 250, 250));
        scrollView.addView(root);

        TextView title = new TextView(this);
        title.setText("表情保存器");
        title.setTextSize(24);
        title.setTextColor(Color.rgb(28, 28, 28));
        title.setGravity(Gravity.START);
        root.addView(title);

        statusText = new TextView(this);
        statusText.setText("搜索相似动图，保存到手机相册");
        statusText.setTextColor(Color.rgb(86, 86, 86));
        statusText.setPadding(0, dp(6), 0, dp(12));
        root.addView(statusText);

        serverInput = new EditText(this);
        serverInput.setSingleLine(true);
        serverInput.setHint("服务端地址，如 http://192.168.1.10:8080");
        serverInput.setText(getSavedServerBaseUrl());
        serverInput.setOnFocusChangeListener((view, hasFocus) -> {
            if (!hasFocus) {
                saveServerBaseUrl(serverInput.getText().toString());
            }
        });
        root.addView(serverInput, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                dp(48)
        ));

        LinearLayout searchRow = new LinearLayout(this);
        searchRow.setOrientation(LinearLayout.HORIZONTAL);
        root.addView(searchRow, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        ));

        searchInput = new EditText(this);
        searchInput.setSingleLine(true);
        searchInput.setHint("输入关键词，如 哈哈、无语、谢谢");
        searchInput.setText("哈哈");
        searchRow.addView(searchInput, new LinearLayout.LayoutParams(0, dp(48), 1));

        Button searchButton = new Button(this);
        searchButton.setText("搜索");
        searchButton.setOnClickListener(v -> search(searchInput.getText().toString(), false));
        searchRow.addView(searchButton, new LinearLayout.LayoutParams(dp(88), dp(48)));

        Button pickButton = new Button(this);
        pickButton.setText("选择本地 GIF/图片保存");
        pickButton.setOnClickListener(v -> pickLocalImage());
        root.addView(pickButton, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                dp(48)
        ));

        progressBar = new ProgressBar(this);
        progressBar.setVisibility(View.GONE);
        root.addView(progressBar);

        TextView recentTitle = sectionTitle("最近保存");
        root.addView(recentTitle);
        recentList = new LinearLayout(this);
        recentList.setOrientation(LinearLayout.VERTICAL);
        root.addView(recentList);

        TextView resultTitle = sectionTitle("搜索结果");
        root.addView(resultTitle);
        resultsList = new LinearLayout(this);
        resultsList.setOrientation(LinearLayout.VERTICAL);
        root.addView(resultsList);

        setContentView(scrollView);
    }

    private TextView sectionTitle(String text) {
        TextView title = new TextView(this);
        title.setText(text);
        title.setTextSize(18);
        title.setTextColor(Color.rgb(40, 40, 40));
        title.setPadding(0, dp(18), 0, dp(8));
        return title;
    }

    private void search(String query, boolean silent) {
        String trimmed = query == null ? "" : query.trim();
        saveServerBaseUrl(serverInput.getText().toString());
        setLoading(true, silent ? "正在加载热门表情..." : "正在搜索...");
        executor.execute(() -> {
            try {
                String url = getSavedServerBaseUrl() + "/api/stickers/search?q=" + Uri.encode(trimmed) + "&page=1";
                String body = new String(downloadBytes(url, null), "UTF-8");
                List<Sticker> stickers = parseStickers(body);
                runOnUiThread(() -> {
                    setLoading(false, stickers.isEmpty() ? "没有找到结果，换个关键词试试" : "找到 " + stickers.size() + " 个表情");
                    renderResults(stickers);
                });
            } catch (Exception exception) {
                runOnUiThread(() -> {
                    setLoading(false, "搜索失败：" + exception.getMessage());
                    Toast.makeText(this, "搜索失败，请确认服务端已启动", Toast.LENGTH_LONG).show();
                });
            }
        });
    }

    private List<Sticker> parseStickers(String body) throws Exception {
        JSONObject root = new JSONObject(body);
        JSONArray items = root.optJSONArray("items");
        List<Sticker> stickers = new ArrayList<>();
        if (items == null) {
            return stickers;
        }
        for (int i = 0; i < items.length(); i++) {
            JSONObject item = items.getJSONObject(i);
            String originalUrl = item.optString("originalUrl", "");
            String thumbnailUrl = item.optString("thumbnailUrl", originalUrl);
            if (!TextUtils.isEmpty(originalUrl)) {
                stickers.add(new Sticker(
                        item.optString("title", "Untitled sticker"),
                        thumbnailUrl,
                        originalUrl,
                        item.optString("source", "GIPHY"),
                        item.optString("mimeType", "image/gif")
                ));
            }
        }
        return stickers;
    }

    private void renderResults(List<Sticker> stickers) {
        resultsList.removeAllViews();
        for (Sticker sticker : stickers) {
            resultsList.addView(createStickerRow(sticker));
        }
    }

    private View createStickerRow(Sticker sticker) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setPadding(0, dp(8), 0, dp(8));
        row.setGravity(Gravity.CENTER_VERTICAL);

        FrameLayout previewFrame = new FrameLayout(this);
        previewFrame.setBackgroundColor(Color.WHITE);
        GifMovieView gifView = new GifMovieView(this);
        previewFrame.addView(gifView, new FrameLayout.LayoutParams(dp(112), dp(112), Gravity.CENTER));
        row.addView(previewFrame, new LinearLayout.LayoutParams(dp(120), dp(120)));

        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(dp(12), 0, 0, 0);
        row.addView(content, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));

        TextView title = new TextView(this);
        title.setText(sticker.title);
        title.setTextColor(Color.rgb(36, 36, 36));
        title.setTextSize(15);
        title.setMaxLines(2);
        content.addView(title);

        TextView source = new TextView(this);
        source.setText(sticker.source + " · GIF");
        source.setTextColor(Color.rgb(100, 100, 100));
        source.setPadding(0, dp(4), 0, dp(6));
        content.addView(source);

        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.HORIZONTAL);
        content.addView(actions);

        Button saveButton = new Button(this);
        saveButton.setText("保存");
        saveButton.setOnClickListener(v -> saveRemoteSticker(sticker));
        actions.addView(saveButton, new LinearLayout.LayoutParams(dp(86), dp(44)));

        Button openButton = new Button(this);
        openButton.setText("打开");
        openButton.setOnClickListener(v -> startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(sticker.originalUrl))));
        actions.addView(openButton, new LinearLayout.LayoutParams(dp(86), dp(44)));

        executor.execute(() -> {
            try {
                byte[] data = downloadBytes(sticker.thumbnailUrl, sticker.mimeType);
                runOnUiThread(() -> gifView.setBytes(data));
            } catch (Exception ignored) {
                runOnUiThread(() -> source.setText(sticker.source + " · 预览失败"));
            }
        });

        return row;
    }

    private void saveRemoteSticker(Sticker sticker) {
        setLoading(true, "正在下载并保存...");
        executor.execute(() -> {
            try {
                byte[] data = downloadBytes(sticker.originalUrl, sticker.mimeType);
                String extension = sticker.mimeType.contains("gif") ? ".gif" : ".jpg";
                Uri saved = saveToGallery(data, sticker.mimeType, extension);
                rememberSaved(sticker.title);
                runOnUiThread(() -> {
                    setLoading(false, "已保存到相册：" + saved);
                    loadRecentSaves();
                    Toast.makeText(this, "已保存到相册", Toast.LENGTH_SHORT).show();
                });
            } catch (Exception exception) {
                runOnUiThread(() -> {
                    setLoading(false, "保存失败：" + exception.getMessage());
                    Toast.makeText(this, "保存失败", Toast.LENGTH_LONG).show();
                });
            }
        });
    }

    private byte[] downloadBytes(String urlText, String expectedMime) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(urlText).openConnection();
        connection.setConnectTimeout(12000);
        connection.setReadTimeout(20000);
        connection.setRequestProperty("User-Agent", "StickerSaver/1.0");
        int code = connection.getResponseCode();
        if (code < 200 || code >= 300) {
            throw new IllegalStateException("HTTP " + code);
        }
        String contentType = connection.getContentType();
        if (expectedMime != null && contentType != null && !contentType.toLowerCase(Locale.US).startsWith("image/")) {
            throw new IllegalStateException("不是图片资源");
        }
        try (InputStream input = connection.getInputStream()) {
            return readAll(input);
        } finally {
            connection.disconnect();
        }
    }

    private byte[] readAll(InputStream input) throws Exception {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        int read;
        while ((read = input.read(buffer)) != -1) {
            output.write(buffer, 0, read);
        }
        return output.toByteArray();
    }

    private Uri saveToGallery(byte[] data, String mimeType, String extension) throws Exception {
        String displayName = "sticker_" + new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(new Date()) + extension;
        ContentValues values = new ContentValues();
        values.put(MediaStore.MediaColumns.DISPLAY_NAME, displayName);
        values.put(MediaStore.MediaColumns.MIME_TYPE, mimeType);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            values.put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_PICTURES + "/StickerSaver");
            values.put(MediaStore.MediaColumns.IS_PENDING, 1);
        }

        ContentResolver resolver = getContentResolver();
        Uri collection = MediaStore.Images.Media.EXTERNAL_CONTENT_URI;
        Uri uri = resolver.insert(collection, values);
        if (uri == null) {
            throw new IllegalStateException("无法创建相册文件");
        }
        try (OutputStream output = resolver.openOutputStream(uri)) {
            if (output == null) {
                throw new IllegalStateException("无法写入相册文件");
            }
            output.write(data);
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            values.clear();
            values.put(MediaStore.MediaColumns.IS_PENDING, 0);
            resolver.update(uri, values, null, null);
        }
        return uri;
    }

    private void pickLocalImage() {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("image/*");
        startActivityForResult(intent, PICK_IMAGE_REQUEST);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == PICK_IMAGE_REQUEST && resultCode == RESULT_OK && data != null && data.getData() != null) {
            saveSharedUri(data.getData());
        }
    }

    private void handleIncomingIntent(Intent intent) {
        if (intent == null) {
            return;
        }
        String action = intent.getAction();
        if (Intent.ACTION_SEND.equals(action)) {
            Uri stream = intent.getParcelableExtra(Intent.EXTRA_STREAM);
            if (stream != null) {
                saveSharedUri(stream);
                return;
            }
            CharSequence text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT);
            if (!TextUtils.isEmpty(text)) {
                search(text.toString(), false);
            }
        } else if (Intent.ACTION_SEND_MULTIPLE.equals(action)) {
            ArrayList<Uri> streams = intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
            if (streams != null) {
                for (Uri uri : streams) {
                    saveSharedUri(uri);
                }
            }
        }
    }

    private void saveSharedUri(Uri uri) {
        setLoading(true, "正在保存分享的图片...");
        executor.execute(() -> {
            try (InputStream input = getContentResolver().openInputStream(uri)) {
                if (input == null) {
                    throw new IllegalStateException("无法读取分享文件");
                }
                String mimeType = getContentResolver().getType(uri);
                if (mimeType == null || !mimeType.startsWith("image/")) {
                    mimeType = "image/gif";
                }
                String extension = mimeType.contains("gif") ? ".gif" : ".jpg";
                saveToGallery(readAll(input), mimeType, extension);
                rememberSaved("分享的表情");
                runOnUiThread(() -> {
                    setLoading(false, "已保存分享的图片");
                    loadRecentSaves();
                    Toast.makeText(this, "已保存到相册", Toast.LENGTH_SHORT).show();
                });
            } catch (Exception exception) {
                runOnUiThread(() -> {
                    setLoading(false, "分享保存失败：" + exception.getMessage());
                    Toast.makeText(this, "保存失败", Toast.LENGTH_LONG).show();
                });
            }
        });
    }

    private void rememberSaved(String title) {
        SharedPreferences preferences = getSharedPreferences("recent", MODE_PRIVATE);
        String previous = preferences.getString("items", "");
        String item = new SimpleDateFormat("MM-dd HH:mm", Locale.US).format(new Date()) + " · " + title;
        String combined = TextUtils.isEmpty(previous) ? item : item + "\n" + previous;
        String[] lines = combined.split("\n");
        StringBuilder trimmed = new StringBuilder();
        for (int i = 0; i < Math.min(lines.length, 8); i++) {
            if (i > 0) {
                trimmed.append('\n');
            }
            trimmed.append(lines[i]);
        }
        preferences.edit().putString("items", trimmed.toString()).apply();
    }

    private void loadRecentSaves() {
        recentList.removeAllViews();
        String items = getSharedPreferences("recent", MODE_PRIVATE).getString("items", "");
        if (TextUtils.isEmpty(items)) {
            TextView empty = new TextView(this);
            empty.setText("还没有保存记录");
            empty.setTextColor(Color.rgb(120, 120, 120));
            recentList.addView(empty);
            return;
        }
        for (String line : items.split("\n")) {
            TextView view = new TextView(this);
            view.setText(line);
            view.setTextColor(Color.rgb(72, 72, 72));
            view.setPadding(0, dp(3), 0, dp(3));
            recentList.addView(view);
        }
    }

    private String getSavedServerBaseUrl() {
        String value = getSharedPreferences("settings", MODE_PRIVATE)
                .getString("serverBaseUrl", DEFAULT_SERVER_BASE_URL);
        if (value == null || value.trim().isEmpty()) {
            return DEFAULT_SERVER_BASE_URL;
        }
        return trimTrailingSlash(value.trim());
    }

    private void saveServerBaseUrl(String value) {
        String normalized = value == null || value.trim().isEmpty()
                ? DEFAULT_SERVER_BASE_URL
                : trimTrailingSlash(value.trim());
        getSharedPreferences("settings", MODE_PRIVATE)
                .edit()
                .putString("serverBaseUrl", normalized)
                .apply();
    }

    private String trimTrailingSlash(String value) {
        while (value.endsWith("/") && value.length() > "https://".length()) {
            value = value.substring(0, value.length() - 1);
        }
        return value;
    }

    private void setLoading(boolean loading, String message) {
        progressBar.setVisibility(loading ? View.VISIBLE : View.GONE);
        statusText.setText(message);
    }

    private void ensureLegacyStoragePermission() {
        if (Build.VERSION.SDK_INT <= Build.VERSION_CODES.P
                && checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE}, STORAGE_PERMISSION_REQUEST);
        }
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private static class Sticker {
        final String title;
        final String thumbnailUrl;
        final String originalUrl;
        final String source;
        final String mimeType;

        Sticker(String title, String thumbnailUrl, String originalUrl, String source, String mimeType) {
            this.title = title;
            this.thumbnailUrl = thumbnailUrl;
            this.originalUrl = originalUrl;
            this.source = source;
            this.mimeType = mimeType;
        }
    }

    public static class GifMovieView extends View {
        private Movie movie;
        private long startTime;

        public GifMovieView(android.content.Context context) {
            super(context);
        }

        public void setBytes(byte[] bytes) {
            movie = Movie.decodeByteArray(bytes, 0, bytes.length);
            startTime = android.os.SystemClock.uptimeMillis();
            invalidate();
        }

        @Override
        protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            if (movie == null) {
                canvas.drawColor(Color.rgb(245, 245, 245));
                return;
            }
            int duration = movie.duration();
            if (duration <= 0) {
                duration = 1000;
            }
            int relTime = (int) ((android.os.SystemClock.uptimeMillis() - startTime) % duration);
            movie.setTime(relTime);
            float scale = Math.min(
                    getWidth() / Math.max(1f, movie.width()),
                    getHeight() / Math.max(1f, movie.height())
            );
            canvas.save();
            canvas.translate((getWidth() - movie.width() * scale) / 2f, (getHeight() - movie.height() * scale) / 2f);
            canvas.scale(scale, scale);
            movie.draw(canvas, 0, 0);
            canvas.restore();
            invalidate();
        }
    }
}
