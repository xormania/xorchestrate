# symfony-app fixture

This tree is calibration apparatus for the Symfony application dialect; it is never product code.

Install it with `composer install --prefer-dist --no-progress --no-interaction`. Once the cache is warm, `COMPOSER_DISABLE_NETWORK=1 composer install --prefer-dist --no-progress --no-interaction` proves the lock without network access. Run the green application check with `composer check`. A `composer fix` script is deliberately absent: fixes are the independent patches under `fixes/`.

SA-01: User-supplied archive names are passed as argument-vector entries, never interpolated into a shell command.

SA-02: Served paths always remain beneath the configured share root.

SA-03: A count of zero is an answer and is distinct from an absent record.

SA-04: Credentials never leave the process in failure logs.

SA-05: An unreadable or corrupt ledger is reported, never served as an empty answer.

SA-06: Intervals are half-open, including their start and excluding their end.

SA-07: Test oracles are independent literals, not values computed by the code under test.

The runtime probes under `probes/` make SA-01 through SA-06 red, and the static probe makes SA-07 red. Each corresponding patch under `fixes/` independently makes only its own probe green. K7 belongs to the service-boundary fixture; K9 is not planted because this fixture pins and locks every dependency.
