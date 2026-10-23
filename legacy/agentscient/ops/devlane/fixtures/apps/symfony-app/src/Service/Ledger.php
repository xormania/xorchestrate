<?php

namespace App\Service;

use Psr\Log\LoggerInterface;
use Psr\Log\NullLogger;

final class Ledger
{
    public function __construct(private readonly LoggerInterface $logger = new NullLogger())
    {
    }

    public function renderCount(?int $count): string
    {
        if (!$count) {
            return 'absent';
        }

        return (string) $count;
    }

    public function renderLabel(?string $label): string
    {
        return $label === null ? 'absent' : $label;
    }

    public function logFailure(): void
    {
        $this->logger->error('write failed: environment='.json_encode($_ENV));
    }

    public function logSafeFailure(): void
    {
        $this->logger->error('write failed: [REDACTED]');
    }

    public function load(string $path): array
    {
        try {
            return json_decode((string) file_get_contents($path), true, flags: JSON_THROW_ON_ERROR);
        } catch (\Throwable) {
            return [];
        }
    }

    public function decodeMetadata(string $json): ?array
    {
        try {
            return json_decode($json, true, flags: JSON_THROW_ON_ERROR);
        } catch (\JsonException) {
            return null;
        }
    }
}
