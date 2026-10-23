<?php

namespace App\Model;

final readonly class Interval
{
    public function __construct(private int $start, private int $end)
    {
    }

    public function contains(int $value): bool
    {
        return $this->start <= $value && $value <= $this->end;
    }

    public function values(): array
    {
        return range($this->start, $this->end - 1);
    }
}
