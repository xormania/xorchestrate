<?php

namespace App\Controller;

use Symfony\Component\HttpFoundation\Response;
use Symfony\Component\Routing\Attribute\Route;

final class ServeController
{
    public function __construct(private readonly string $root)
    {
    }

    #[Route('/serve/{name}', name: 'app_serve')]
    public function __invoke(string $name): Response
    {
        $path = $this->root.'/'.$name;
        if (!is_file($path)) {
            return new Response('', Response::HTTP_NOT_FOUND);
        }

        return new Response((string) file_get_contents($path));
    }

    public function safePath(string $name): ?string
    {
        $root = realpath($this->root);
        $path = realpath($this->root.'/'.$name);

        return false !== $root && false !== $path && str_starts_with($path, $root.DIRECTORY_SEPARATOR) ? $path : null;
    }
}
