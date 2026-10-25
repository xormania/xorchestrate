<?php

namespace App\Probes;

use Psr\Log\AbstractLogger;

final class RecordingLogger extends AbstractLogger
{
    public array $messages = [];

    public function log($level, string|\Stringable $message, array $context = []): void
    {
        $this->messages[] = (string) $message;
    }
}
