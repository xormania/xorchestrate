use std::fs;

use fixture_rust_crate::store::{read_records, write_records, Record};

#[test]
fn records_round_trip() {
    let root = std::env::temp_dir().join(format!("fixture-store-{}", std::process::id()));
    fs::create_dir_all(&root).expect("create temporary store");
    let path = root.join("records");
    let records = vec![Record::new(2, "actor".to_owned())];
    write_records(&path, &records).expect("write records");
    assert_eq!(read_records(&path).expect("read records"), records);
    fs::remove_dir_all(root).expect("remove temporary store");
}
