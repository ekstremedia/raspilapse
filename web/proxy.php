<?php
/**
 * Optimized image proxy for 24/7 kiosk operation
 * - Uses file_get_contents with proper stream context
 * - Proper timeouts, error handling, and cleanup
 */

// Disable output buffering for clean response
while (ob_get_level()) {
    ob_end_clean();
}

// Prevent any caching
header("Cache-Control: no-store, no-cache, must-revalidate, max-age=0");
header("Pragma: no-cache");
header("Expires: 0");

// Remote image URL with cache buster
$timestamp = time() . mt_rand();
$url = "https://ekstremedia.no/storage/camera-current/spjutvika_01_latest.jpg?t=" . $timestamp;

// Configure stream context with timeouts and proper SSL handling
$context = stream_context_create([
    'http' => [
        'method' => 'GET',
        'timeout' => 15,                    // Total timeout in seconds
        'ignore_errors' => false,
        'header' => [
            'Accept: image/jpeg, image/*',
            'Connection: close',
            'User-Agent: KioskProxy/1.0'
        ]
    ],
    'ssl' => [
        'verify_peer' => true,
        'verify_peer_name' => true,
        'allow_self_signed' => false
    ]
]);

// Set default socket timeout as backup
$oldTimeout = ini_get('default_socket_timeout');
ini_set('default_socket_timeout', 15);

// Attempt to fetch the image
$imageData = @file_get_contents($url, false, $context);

// Restore original timeout
ini_set('default_socket_timeout', $oldTimeout);

// Check for errors
if ($imageData === false) {
    // Return a 1x1 transparent pixel as fallback to prevent broken images
    header("Content-Type: image/gif");
    header("Content-Length: 43");
    header("X-Proxy-Error: Failed to fetch remote image");
    // Minimal valid 1x1 transparent GIF
    echo base64_decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7");
    exit;
}

// Verify we got actual image data (JPEG starts with FFD8)
$isValidJpeg = strlen($imageData) > 2 &&
               ord($imageData[0]) === 0xFF &&
               ord($imageData[1]) === 0xD8;

if (!$isValidJpeg) {
    header("Content-Type: image/gif");
    header("Content-Length: 43");
    header("X-Proxy-Error: Invalid image data received");
    echo base64_decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7");
    exit;
}

// Send successful response with proper headers
header("Content-Type: image/jpeg");
header("Content-Length: " . strlen($imageData));
header("Connection: close");

// Output image data
echo $imageData;

// Explicit cleanup
unset($imageData);

// Flush output
flush();

// FastCGI finish if available
if (function_exists('fastcgi_finish_request')) {
    fastcgi_finish_request();
}
