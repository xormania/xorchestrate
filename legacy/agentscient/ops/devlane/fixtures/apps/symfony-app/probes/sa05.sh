#!/bin/sh
exec vendor/bin/phpunit --no-configuration --bootstrap tests/bootstrap.php probes/Sa05Test.php
