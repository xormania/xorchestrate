<?php

use App\Command\ArchiveCommand;
use PHPUnit\Framework\TestCase;
use Symfony\Component\Console\Tester\CommandTester;

final class Sa01Test extends TestCase
{
    public function testArchiveNameCannotRunAShellCommand(): void
    {
        $marker = sys_get_temp_dir().'/sa01-'.bin2hex(random_bytes(6));
        try {
            (new CommandTester(new ArchiveCommand()))->execute(['name' => '; touch '.$marker]);
            self::assertFileDoesNotExist($marker);
        } finally {
            @unlink($marker);
        }
    }
}
