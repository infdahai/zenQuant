// Regenerate from repository root using rustc with rust/target/debug/deps and
// --extern options_core=rust/target/debug/liboptions_core.rlib, then run the binary.
use options_core::{canonical::{canonical_bytes, canonical_hash},codec,paper::{Journal,Command,Confirmation}};
fn main(){
 let mut j=Journal::default();
 for bytes in [include_bytes!("../../../rust/options-workbench/fixtures/manual-paper-settings.json").as_slice(),include_bytes!("../../../rust/options-workbench/fixtures/manual-paper-intent.json").as_slice()] {
  let c:Command=codec::decode(bytes,1048576).unwrap();
  let p=j.preview(c,1800000000000).unwrap();
  j=j.confirm(&Confirmation{preview:p,acknowledged_record_only:true},1800000000000).unwrap().0;
 }
 let journal=String::from_utf8(canonical_bytes(&j).unwrap()).unwrap();
 println!("{{\"journal\":{},\"sha256\":\"{}\"}}",journal,canonical_hash(&j).unwrap());
}
