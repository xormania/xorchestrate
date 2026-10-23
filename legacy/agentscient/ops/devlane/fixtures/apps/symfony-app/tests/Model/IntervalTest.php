<?php

namespace App\Tests\Model;

use App\Model\Interval;
use PHPUnit\Framework\TestCase;

final class IntervalTest extends TestCase
{
    public function testValuesAreHalfOpen(): void
    {
        self::assertSame([1, 2], (new Interval(1, 3))->values());
    }
}
