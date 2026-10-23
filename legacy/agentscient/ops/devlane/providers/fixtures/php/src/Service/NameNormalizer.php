<?php

namespace App\Service;

final class NameNormalizer
{
    public function normalize(string $name): string
    {
        return strtolower($name);
    }
}
