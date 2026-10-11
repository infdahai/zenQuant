//! Release-mode bounded batch baseline; no cached model, I/O or browser in timed loop.
use options_core::{fixtures::mathematical_curve, payoff_curve};
use std::{hint::black_box, time::Instant};
fn main() {
    println!("points,repeats,median_us,min_us,max_us,points_per_second");
    for count in [2, 64, 512, 4096] {
        let mut r = mathematical_curve().unwrap();
        r.grid.as_mut().unwrap().count = count;
        black_box(payoff_curve::analyze(&r).unwrap());
        let mut times = vec![];
        for _ in 0..15 {
            let now = Instant::now();
            black_box(payoff_curve::analyze(black_box(&r)).unwrap());
            times.push(now.elapsed().as_micros());
        }
        times.sort();
        let median = times[7];
        println!(
            "{count},15,{median},{},{},{}",
            times[0],
            times[14],
            count as u128 * 1_000_000 / median.max(1)
        );
    }
}
