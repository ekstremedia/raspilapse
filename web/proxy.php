<?php
// Prevent browser/proxy caching
header("Cache-Control: no-store, no-cache, must-revalidate, max-age=0");
header("Pragma: no-cache");
header("Expires: 0");

// Get the image from the remote server with cache-busting
$timestamp = time() . mt_rand();
$img = file_get_contents("https://ekstremedia.no/storage/camera-current/spjutvika_01_latest.jpg?t=" . $timestamp);

// Tell the tablet it's an image
header("Content-Type: image/jpeg");
echo $img;
?>
