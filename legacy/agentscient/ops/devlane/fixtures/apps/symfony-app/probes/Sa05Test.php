<?php

use App\Service\Ledger;
use App\Service\LedgerException;
use PHPUnit\Framework\TestCase;

final class Sa05Test extends TestCase
{
    public function testCorruptLedgerIsReported(): void
    {
        $path = tempnam(sys_get_temp_dir(), 'sa05-');
        file_put_contents($path, '{broken');
        try {
            (new Ledger())->load($path);
            self::fail('Expected LedgerException');
        } catch (LedgerException) {
            self::assertTrue(true);
        } finally {
            @unlink($path);
        }
    }
}
