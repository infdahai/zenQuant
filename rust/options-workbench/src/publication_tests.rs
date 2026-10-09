use super::*;
use std::fs;
fn temp() -> std::path::PathBuf {
    static N: AtomicU64 = AtomicU64::new(0);
    let p = fs::canonicalize(std::env::temp_dir())
        .unwrap()
        .join(format!(
            "options-publish-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::Relaxed)
        ));
    fs::create_dir(&p).unwrap();
    p
}
fn artifacts() -> Vec<(&'static str, Vec<u8>)> {
    vec![
        ("report.json", b"{}".to_vec()),
        ("REPORT.md", b"diagnostic".to_vec()),
        ("identity.json", b"{}".to_vec()),
    ]
}
#[test]
fn failures_retain_identifiable_stage_and_durability_failure_preserves_destination() {
    for point in [
        "write",
        "file_fsync",
        "stage_fsync",
        "rename",
        "parent_fsync",
    ] {
        let p = temp();
        let out = p.join("output");
        let target = crate::io::validate_output(&out, &[]).unwrap();
        let result = publish_internal(
            &target,
            &artifacts(),
            PublicationHooks {
                fail_at: Some(point),
                ..Default::default()
            },
        );
        let incomplete = fs::read_dir(&p)
            .unwrap()
            .filter(|e| {
                e.as_ref()
                    .unwrap()
                    .file_name()
                    .to_string_lossy()
                    .starts_with(".options-workbench-incomplete-")
            })
            .count();
        if point == "parent_fsync" {
            assert_eq!(result, Err(Failure::PublishedDurabilityUnconfirmed));
            assert!(out.join("report.json").is_file());
            assert_eq!(incomplete, 0)
        } else {
            assert_eq!(result, Err(Failure::Incomplete));
            assert!(!out.exists());
            assert_eq!(incomplete, 1)
        }
        fs::remove_dir_all(p).unwrap();
    }
}
#[test]
fn rename_race_preserves_the_racing_target() {
    fn race(out: &Path) {
        fs::create_dir(out).unwrap();
        fs::write(out.join("sentinel"), b"winner").unwrap();
    }
    let p = temp();
    let out = p.join("output");
    let target = crate::io::validate_output(&out, &[]).unwrap();
    assert_eq!(
        publish_internal(
            &target,
            &artifacts(),
            PublicationHooks {
                before_rename: Some(race),
                ..Default::default()
            }
        ),
        Err(Failure::Incomplete)
    );
    assert_eq!(fs::read(out.join("sentinel")).unwrap(), b"winner");
    assert!(!out.join("report.json").exists());
    fs::remove_dir_all(p).unwrap();
}
#[test]
fn parent_swap_between_preflight_and_publication_is_closed() {
    let p = temp();
    let parent = p.join("parent");
    fs::create_dir(&parent).unwrap();
    let out = parent.join("report");
    let target = crate::io::validate_output(&out, &[]).unwrap();
    fs::rename(&parent, p.join("old")).unwrap();
    fs::create_dir(&parent).unwrap();
    assert_eq!(publish(&target, &artifacts()), Err(Failure::Incomplete));
    assert_eq!(fs::read_dir(&parent).unwrap().count(), 0);
    assert_eq!(fs::read_dir(p.join("old")).unwrap().count(), 0);
    fs::remove_dir_all(p).unwrap();
}
#[test]
fn parent_swap_before_rename_keeps_identifiable_stage_in_held_parent() {
    fn swap(out: &Path) {
        let parent = out.parent().unwrap();
        fs::rename(parent, parent.with_file_name("old")).unwrap();
        fs::create_dir(parent).unwrap();
    }
    let p = temp();
    let parent = p.join("parent");
    fs::create_dir(&parent).unwrap();
    let target = crate::io::validate_output(&parent.join("report"), &[]).unwrap();
    assert_eq!(
        publish_internal(
            &target,
            &artifacts(),
            PublicationHooks {
                before_rename: Some(swap),
                ..Default::default()
            }
        ),
        Err(Failure::Incomplete)
    );
    assert_eq!(fs::read_dir(parent).unwrap().count(), 0);
    assert_eq!(fs::read_dir(p.join("old")).unwrap().count(), 1);
    fs::remove_dir_all(p).unwrap();
}
#[test]
fn bounded_reader_rejects_limit_plus_one_symlinks_and_nonregular() {
    let p = temp();
    let r = p.join("r");
    fs::write(&r, b"abcd").unwrap();
    assert_eq!(read_bounded(&r, 3), Err(Failure::Invalid));
    assert_eq!(read_bounded(&r, 4).unwrap(), b"abcd");
    let link = p.join("link");
    std::os::unix::fs::symlink(&r, &link).unwrap();
    assert_eq!(read_bounded(&link, 4), Err(Failure::Invalid));
    assert_eq!(read_bounded(&p, 4), Err(Failure::Invalid));
    fs::remove_dir_all(p).unwrap();
}
