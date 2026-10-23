<?php

require_once __DIR__.'/RecordingLogger.php';

use App\Probes\RecordingLogger;
use App\Service\Ledger;
use PHPUnit\Framework\TestCase;

final class Sa04Test extends TestCase
{
    public function testFailureLogDoesNotContainTheToken(): void
    {
        $before = $_ENV['FIXTURE_TOKEN'] ?? null;
        $_ENV['FIXTURE_TOKEN'] = 'fixture-secret';
        try {
            $logger = new RecordingLogger();
            (new Ledger($logger))->logFailure();
            self::assertStringNotContainsString('fixture-secret', implode('\n', $logger->messages));
        } finally {
            if (null === $before) {
                unset($_ENV['FIXTURE_TOKEN']);
            } else {
                $_ENV['FIXTURE_TOKEN'] = $before;
            }
        }
    }
}
