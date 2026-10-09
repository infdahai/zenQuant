use options_core::decimal::Dec;
#[test]
fn canonical_decimal() {
    assert_eq!(Dec::parse(".01").unwrap().to_string(), "0.01");
    assert_eq!(Dec::parse("-0.000").unwrap().to_string(), "0");
}
#[test]
fn finite_exact_division() {
    let one = Dec::parse("1").unwrap();
    assert_eq!(
        one.checked_div(&Dec::parse("8").unwrap())
            .unwrap()
            .to_string(),
        "0.125"
    );
    assert!(one.checked_div(&Dec::parse("3").unwrap()).is_err());
}
#[test]
fn bounded_strict_input() {
    for bad in [
        "1e2",
        "NaN",
        "inf",
        " 1",
        "+1",
        "1.",
        "",
        "0.00000000000000000000000000001",
    ] {
        assert!(Dec::parse(bad).is_err(), "{bad}");
    }
    assert!(Dec::parse(&"9".repeat(65)).is_err());
    assert_eq!(
        Dec::parse(&"9".repeat(64)).unwrap().to_string(),
        "9".repeat(64)
    );
}
#[test]
fn exact_high_magnitude_and_closed_overflow() {
    let huge = Dec::parse(&"9".repeat(64)).unwrap();
    let result = huge.checked_mul(&huge).unwrap();
    assert_eq!(
        result.to_string(),
        format!("{}8{}1", "9".repeat(63), "0".repeat(63))
    );
    let mut value = huge;
    for _ in 0..3 {
        value = value.checked_mul(&value).unwrap();
    }
    assert!(
        value
            .checked_mul(&value)
            .unwrap()
            .checked_mul(&value)
            .is_err()
    );
}
#[test]
fn fraction_limit_and_zero_division() {
    let mut x = Dec::parse("0.0000000000000000000000000001").unwrap();
    for _ in 0..3 {
        x = x.checked_mul(&x).unwrap();
    }
    assert!(x.checked_mul(&x).is_err());
    assert!(Dec::one().checked_div(&Dec::zero()).is_err());
    assert_eq!(
        serde_json::to_string(&Dec::parse("-0").unwrap()).unwrap(),
        "\"0\""
    );
}
