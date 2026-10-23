<?php

use App\Controller\ServeController;
use PHPUnit\Framework\TestCase;

final class Sa02Test extends TestCase
{
    public function testServeCannotEscapeTheShareRoot(): void
    {
        $base = sys_get_temp_dir().'/sa02-'.bin2hex(random_bytes(6));
        mkdir($base.'/share', recursive: true);
        file_put_contents($base.'/outside.txt', 'outside');
        try {
            $response = (new ServeController($base.'/share'))('../outside.txt');
            self::assertNotSame('outside', $response->getContent());
        } finally {
            @unlink($base.'/outside.txt');
            @rmdir($base.'/share');
            @rmdir($base);
        }
    }
}
