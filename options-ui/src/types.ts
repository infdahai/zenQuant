export type Point = { delivery_price: string; gross_payoff: string | null; conditional_model_pnl: string | null };
type Status = {status: string; reason: string};
export type Response = {
  calculation_us: number; request_json: string;
  report: {
    report_currency: string; cutoff_ns: string; expiry_ns: string | null; terminal_fee_assumption: string | null; input_hash: string; grid_hash: string | null; signal_hash: string | null;
    signal_status: Status; missing: string[]; points: Point[]; limitations: string[];
    diagnostic: null | {
      model_version: string; net_status: Status;
      support: {model_support:Status; account_eligibility:Status; execution_evidence:Status};
      cost: null | { premium_native: string | null; fee_native: string | null; total_report_currency: string | null; status: Status };
    };
  };
};
