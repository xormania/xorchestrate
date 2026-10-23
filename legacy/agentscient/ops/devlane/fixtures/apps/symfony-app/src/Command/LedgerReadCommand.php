<?php

namespace App\Command;

use App\Service\Ledger;
use Symfony\Component\Console\Attribute\AsCommand;
use Symfony\Component\Console\Command\Command;
use Symfony\Component\Console\Input\InputInterface;
use Symfony\Component\Console\Output\OutputInterface;

#[AsCommand(name: 'ledger:read')]
final class LedgerReadCommand extends Command
{
    public function __construct(private readonly Ledger $ledger)
    {
        parent::__construct();
    }

    protected function execute(InputInterface $input, OutputInterface $output): int
    {
        $output->writeln($this->ledger->renderCount(3));

        return Command::SUCCESS;
    }
}
