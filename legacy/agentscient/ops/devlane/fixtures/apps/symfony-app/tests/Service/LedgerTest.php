<?php

namespace App\Tests\Service;

use App\Service\Checksum;
use App\Service\Ledger;
use PHPUnit\Framework\TestCase;

final class LedgerTest extends TestCase
{
    public function testCountIsRendered(): void
    {
        self::assertSame('2', (new Ledger())->renderCount(2));
    }

    public function testKnownChecksum(): void
    {
        $expected = 5;
        self::assertSame($expected, Checksum::of('house'));
    }

    public function testFixtureChecksum(): void
    {
        $expected = Checksum::of('fixture');
        self::assertSame($expected, Checksum::of('fixture'));
    }
}
