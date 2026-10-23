<?php

namespace App\Tests\Command;

use App\Command\LedgerReadCommand;
use App\Service\Ledger;
use PHPUnit\Framework\TestCase;
use Symfony\Component\Console\Tester\CommandTester;

final class LedgerReadCommandTest extends TestCase
{
    public function testItPrintsTheCount(): void
    {
        $tester = new CommandTester(new LedgerReadCommand(new Ledger()));
        self::assertSame(0, $tester->execute([]));
        self::assertStringContainsString('3', $tester->getDisplay());
    }
}
