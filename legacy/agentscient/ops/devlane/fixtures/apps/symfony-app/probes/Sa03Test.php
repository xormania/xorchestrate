<?php

use App\Service\Ledger;
use PHPUnit\Framework\TestCase;

final class Sa03Test extends TestCase
{
    public function testZeroIsRenderedAsAnAnswer(): void
    {
        self::assertSame('0', (new Ledger())->renderCount(0));
    }
}
