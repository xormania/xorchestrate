<?php

// Deliberately select runtime facts: phpinfo() would also expose the environment.
$settings = [];
foreach (['opcache.enable', 'opcache.enable_cli', 'opcache.jit', 'opcache.jit_buffer_size', 'pcre.jit', 'zend.max_allowed_stack_size', 'memory_limit'] as $name) {
    $settings[$name] = ini_get($name);
}

$extensions = [];
foreach (get_loaded_extensions() as $name) {
    $extensions[$name] = phpversion($name);
}
ksort($extensions);

echo json_encode([
    'version' => PHP_VERSION,
    'binary' => PHP_BINARY,
    'binary_sha256' => hash_file('sha256', PHP_BINARY),
    'sapi' => PHP_SAPI,
    'thread_safe' => (bool) PHP_ZTS,
    'debug_build' => (bool) PHP_DEBUG,
    'os' => php_uname('s').' '.php_uname('r').' '.php_uname('m'),
    'loaded_ini' => php_ini_loaded_file(),
    'scanned_ini' => php_ini_scanned_files(),
    'settings' => $settings,
    'extensions' => $extensions,
], JSON_PRETTY_PRINT | JSON_THROW_ON_ERROR)."\n";
