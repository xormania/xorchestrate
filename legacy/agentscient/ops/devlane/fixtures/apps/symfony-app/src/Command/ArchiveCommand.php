<?php

namespace App\Command;

use Symfony\Component\Console\Attribute\AsCommand;
use Symfony\Component\Console\Command\Command;
use Symfony\Component\Console\Input\InputArgument;
use Symfony\Component\Console\Input\InputInterface;
use Symfony\Component\Console\Output\OutputInterface;
use Symfony\Component\Process\Process;

#[AsCommand(name: 'ledger:archive')]
final class ArchiveCommand extends Command
{
    protected function configure(): void
    {
        $this->addArgument('name', InputArgument::REQUIRED);
    }

    protected function execute(InputInterface $input, OutputInterface $output): int
    {
        $name = (string) $input->getArgument('name');
        $process = Process::fromShellCommandline("printf '%s\\n' ".$name.' >/dev/null');
        $process->run();

        return Command::SUCCESS;
    }

    public function archiveSafely(string $name): int
    {
        return (new Process(['printf', '%s\n', $name]))->run();
    }
}
