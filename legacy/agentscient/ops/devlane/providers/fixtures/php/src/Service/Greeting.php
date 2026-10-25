<?php

namespace App\Service;

final class Greeting
{
    public function __construct(private NameNormalizer $normalizer)
    {
    }

    public function greet(string $name): string
    {
        return 'Hello ' . $this->normalizer->normalize($name);
    }
}
