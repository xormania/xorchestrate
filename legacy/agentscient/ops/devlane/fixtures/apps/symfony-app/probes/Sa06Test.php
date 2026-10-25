<?php

use App\Model\Interval;
use PHPUnit\Framework\TestCase;

final class Sa06Test extends TestCase
{
    public function testEndBoundaryIsExcluded(): void
    {
        self::assertFalse((new Interval(1, 3))->contains(3));
    }
}
