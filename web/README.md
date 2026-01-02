# Kiosk Web Display

Web files for the tablet kiosk display showing camera feeds.

## Files

- `tablet.html` - Slideshow page for the kiosk browser app
- `proxy.php` - Image proxy to bypass SSL issues on Android tablet

## How It Works

The Android tablet runs a kiosk browser app that loads `tablet.html`. This page cycles through camera images every 2 minutes with smooth fade transitions.

The `proxy.php` script fetches images from `ekstremedia.no` over HTTPS and serves them over HTTP, solving SSL certificate issues on the tablet's WebView.

### Symlinks

These files are symlinked from `/var/www/html/`:
```
/var/www/html/proxy.php  ->  /home/pi/raspilapse/web/proxy.php
/var/www/html/tablet.html  ->  /home/pi/raspilapse/web/tablet.html
```

Edit files here and changes are immediately live.

## Changes (2026-01-02)

### proxy.php
- Added 15-second timeout to prevent hanging requests
- Added error handling with fallback 1x1 transparent GIF
- Added Content-Length header for proper transfer completion
- Added JPEG validation (checks FFD8 magic bytes)
- Added explicit memory cleanup
- Added `Connection: close` header to prevent keep-alive issues

### tablet.html
- Added fetch timeout (20 seconds) using AbortController
- Fixed blob URL memory leak with proper Map-based tracking
- Added periodic memory cleanup every 5 minutes
- Clear event handlers after use to prevent memory leaks
- Added onerror handler to prevent slideshow blocking

## Configuration

In `tablet.html`, adjust settings in the `config` object:
```javascript
const config = {
    interval: 120000,              // Time between slides (ms)
    transitionDuration: 2000,      // Fade transition duration (ms)
    preloadNext: true,             // Preload next image
    maxRetries: 3,                 // Retry failed loads
    retryDelay: 1000,              // Delay between retries (ms)
    fetchTimeout: 20000,           // Fetch timeout (ms)
    memoryCleanupInterval: 300000  // Memory cleanup interval (ms)
};
```

## Image Sources

Edit the `sources` array in `tablet.html` to change camera feeds:
```javascript
const sources = [
    "http://192.168.1.113/proxy.php",
    "http://192.168.1.113/status.jpg",
    "http://192.168.1.109/status.jpg",
];
```
