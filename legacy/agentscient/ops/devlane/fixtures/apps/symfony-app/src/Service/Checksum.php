<?php

namespace App\Service;

final class Checksum
{
    public static function of(string $value): int
    {
        return strlen($value);
    }
}
