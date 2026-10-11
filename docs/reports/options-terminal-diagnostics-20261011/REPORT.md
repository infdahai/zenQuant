# Offline single-leg terminal scenarios

Diagnostic only. Execution authority: NONE. Rankable: false. Live eligible: false.

Gross payoff is conditional expiry mathematics. Conditional net PnL subtracts displayed opening cost and the explicit total terminal fee assumption; neither is an actual fill or realized PnL. Funding feasibility, account eligibility, exercise lifecycle and execution are not established. No probabilities, EV, early-exit valuation or arbitrage conclusion.

Raw sources are not independently verified. Complete input and diagnostic:

<pre>{
  &quot;schema_version&quot;: &quot;options-terminal-diagnostic-report.rust.v1&quot;,
  &quot;diagnostic_only&quot;: true,
  &quot;rankable&quot;: false,
  &quot;live_eligible&quot;: false,
  &quot;execution_authority&quot;: &quot;NONE&quot;,
  &quot;actual_pnl&quot;: null,
  &quot;paper_pnl&quot;: null,
  &quot;sharpe&quot;: null,
  &quot;capacity&quot;: null,
  &quot;lifecycle_arbitrage_edge&quot;: null,
  &quot;identity&quot;: {
    &quot;request_bytes_sha256&quot;: &quot;e33d022e58df60813b3b7775b43e09757d479cae71d27a08f22926e6b19d70a2&quot;,
    &quot;canonical_input_sha256&quot;: &quot;8ca9a0d59858734c617b255e3bd8e4299daeb0dbcc37809deb596e1b3da49f71&quot;,
    &quot;code_sha256&quot;: &quot;5d916b8002eac22333251f99c10314a605b4fe8ad0377b9bb35ec22c9d56125b&quot;,
    &quot;method_version&quot;: &quot;exact_decimal.rust.v1&quot;,
    &quot;cargo_lock_sha256&quot;: &quot;d8887ca3b9d3012dcf7737ffb7993ea2eb7bc4f54e2b2aca22b06ccb87e82504&quot;,
    &quot;source_identities&quot;: [
      [
        &quot;fixture&quot;,
        &quot;mathematical_fixture_not_market_data&quot;
      ]
    ],
    &quot;verified_raw_sources&quot;: [],
    &quot;raw_source_integrity&quot;: &quot;NOT_INDEPENDENTLY_VERIFIED&quot;,
    &quot;projection_trust&quot;: &quot;MANUAL_UNATTESTED&quot;
  },
  &quot;input&quot;: {
    &quot;schema_version&quot;: &quot;options-terminal-diagnostic-request.rust.v1&quot;,
    &quot;request_id&quot;: &quot;single-long-call&quot;,
    &quot;cutoff_ns&quot;: 10,
    &quot;report_currency&quot;: &quot;USDC&quot;,
    &quot;leg&quot;: {
      &quot;instrument&quot;: {
        &quot;venue&quot;: &quot;MATHEMATICAL_FIXTURE&quot;,
        &quot;product&quot;: &quot;linear_option&quot;,
        &quot;instrument_id&quot;: &quot;ETH-1000-100-C&quot;,
        &quot;underlying&quot;: &quot;ETH&quot;,
        &quot;rule_version&quot;: &quot;mathematical.v1&quot;,
        &quot;spec_schema&quot;: &quot;options-instrument.rust.v1&quot;,
        &quot;spec_hash&quot;: &quot;6bb544f3bccf4e56f85881a251420f89c289c90c3d3c7348ba4b3a2972614c3b&quot;
      },
      &quot;side&quot;: &quot;BUY&quot;,
      &quot;quantity_native&quot;: &quot;0.01&quot;
    },
    &quot;spec&quot;: {
      &quot;venue&quot;: &quot;MATHEMATICAL_FIXTURE&quot;,
      &quot;instrument_id&quot;: &quot;ETH-1000-100-C&quot;,
      &quot;product&quot;: &quot;linear_option&quot;,
      &quot;underlying&quot;: &quot;ETH&quot;,
      &quot;quote_currency&quot;: &quot;USDC&quot;,
      &quot;strike_currency&quot;: &quot;USDC&quot;,
      &quot;settlement_currency&quot;: &quot;USDC&quot;,
      &quot;margin_currency&quot;: &quot;USDC&quot;,
      &quot;expiry_ns&quot;: 1000,
      &quot;strike&quot;: &quot;100&quot;,
      &quot;option_type&quot;: &quot;C&quot;,
      &quot;exercise_style&quot;: &quot;EUROPEAN&quot;,
      &quot;quantity_unit&quot;: &quot;base&quot;,
      &quot;multiplier&quot;: &quot;1&quot;,
      &quot;price_unit&quot;: &quot;quote_per_base&quot;,
      &quot;min_quantity_native&quot;: &quot;0.01&quot;,
      &quot;quantity_step_native&quot;: &quot;0.01&quot;,
      &quot;max_quantity_native&quot;: null,
      &quot;tick_bands&quot;: [
        [
          &quot;0&quot;,
          &quot;0.01&quot;
        ]
      ],
      &quot;settlement_index&quot;: &quot;MATHEMATICAL_ETH&quot;,
      &quot;settlement_window_ns&quot;: 1,
      &quot;settlement_rule&quot;: &quot;linear_cash&quot;,
      &quot;rule_version&quot;: &quot;mathematical.v1&quot;,
      &quot;metadata_hash&quot;: &quot;0000000000000000000000000000000000000000000000000000000000000000&quot;,
      &quot;valid_from_ns&quot;: 0,
      &quot;valid_until_ns&quot;: null,
      &quot;provenance&quot;: &quot;fixture&quot;
    },
    &quot;metadata_known_ns&quot;: 0,
    &quot;scenarios&quot;: {
      &quot;meta&quot;: {
        &quot;object_id&quot;: &quot;independent-terminal-scenarios&quot;,
        &quot;revision&quot;: 1,
        &quot;parent_hash&quot;: null,
        &quot;schema_version&quot;: &quot;options-terminal-scenarios.rust.v1&quot;,
        &quot;method_version&quot;: &quot;exact_decimal.rust.v1&quot;,
        &quot;received_ns&quot;: 10,
        &quot;available_ns&quot;: 10,
        &quot;cutoff_ns&quot;: 10,
        &quot;source_ref&quot;: &quot;mathematical_fixture_not_market_data&quot;,
        &quot;source_sha256&quot;: null,
        &quot;provenance&quot;: &quot;fixture&quot;
      },
      &quot;spec_hash&quot;: &quot;6bb544f3bccf4e56f85881a251420f89c289c90c3d3c7348ba4b3a2972614c3b&quot;,
      &quot;scenarios&quot;: [
        {
          &quot;scenario_id&quot;: &quot;price-0&quot;,
          &quot;at_ns&quot;: 1000,
          &quot;delivery_price&quot;: &quot;0&quot;
        },
        {
          &quot;scenario_id&quot;: &quot;price-90&quot;,
          &quot;at_ns&quot;: 1000,
          &quot;delivery_price&quot;: &quot;90&quot;
        },
        {
          &quot;scenario_id&quot;: &quot;price-100&quot;,
          &quot;at_ns&quot;: 1000,
          &quot;delivery_price&quot;: &quot;100&quot;
        },
        {
          &quot;scenario_id&quot;: &quot;price-110&quot;,
          &quot;at_ns&quot;: 1000,
          &quot;delivery_price&quot;: &quot;110&quot;
        }
      ]
    },
    &quot;quotes&quot;: [
      {
        &quot;quote_id&quot;: &quot;ETH-1000-100-C&quot;,
        &quot;book&quot;: {
          &quot;spec&quot;: {
            &quot;venue&quot;: &quot;MATHEMATICAL_FIXTURE&quot;,
            &quot;instrument_id&quot;: &quot;ETH-1000-100-C&quot;,
            &quot;product&quot;: &quot;linear_option&quot;,
            &quot;underlying&quot;: &quot;ETH&quot;,
            &quot;quote_currency&quot;: &quot;USDC&quot;,
            &quot;strike_currency&quot;: &quot;USDC&quot;,
            &quot;settlement_currency&quot;: &quot;USDC&quot;,
            &quot;margin_currency&quot;: &quot;USDC&quot;,
            &quot;expiry_ns&quot;: 1000,
            &quot;strike&quot;: &quot;100&quot;,
            &quot;option_type&quot;: &quot;C&quot;,
            &quot;exercise_style&quot;: &quot;EUROPEAN&quot;,
            &quot;quantity_unit&quot;: &quot;base&quot;,
            &quot;multiplier&quot;: &quot;1&quot;,
            &quot;price_unit&quot;: &quot;quote_per_base&quot;,
            &quot;min_quantity_native&quot;: &quot;0.01&quot;,
            &quot;quantity_step_native&quot;: &quot;0.01&quot;,
            &quot;max_quantity_native&quot;: null,
            &quot;tick_bands&quot;: [
              [
                &quot;0&quot;,
                &quot;0.01&quot;
              ]
            ],
            &quot;settlement_index&quot;: &quot;MATHEMATICAL_ETH&quot;,
            &quot;settlement_window_ns&quot;: 1,
            &quot;settlement_rule&quot;: &quot;linear_cash&quot;,
            &quot;rule_version&quot;: &quot;mathematical.v1&quot;,
            &quot;metadata_hash&quot;: &quot;0000000000000000000000000000000000000000000000000000000000000000&quot;,
            &quot;valid_from_ns&quot;: 0,
            &quot;valid_until_ns&quot;: null,
            &quot;provenance&quot;: &quot;fixture&quot;
          },
          &quot;bids&quot;: [
            {
              &quot;price&quot;: &quot;1.9&quot;,
              &quot;quantity_native&quot;: &quot;1&quot;
            }
          ],
          &quot;asks&quot;: [
            {
              &quot;price&quot;: &quot;2&quot;,
              &quot;quantity_native&quot;: &quot;1&quot;
            }
          ],
          &quot;source_ns&quot;: 10,
          &quot;received_ns&quot;: 10,
          &quot;available_ns&quot;: 10,
          &quot;raw_hash&quot;: &quot;0000000000000000000000000000000000000000000000000000000000000000&quot;
        },
        &quot;index_in_quote&quot;: {
          &quot;value&quot;: &quot;100&quot;,
          &quot;source_ns&quot;: 10,
          &quot;received_ns&quot;: 10,
          &quot;available_ns&quot;: 10,
          &quot;source_id&quot;: &quot;mathematical_fixture_not_market_data&quot;,
          &quot;provenance&quot;: &quot;fixture&quot;
        },
        &quot;index_currency&quot;: &quot;USDC&quot;,
        &quot;quote_to_report&quot;: {
          &quot;value&quot;: &quot;1&quot;,
          &quot;source_ns&quot;: 10,
          &quot;received_ns&quot;: 10,
          &quot;available_ns&quot;: 10,
          &quot;source_id&quot;: &quot;mathematical_fixture_not_market_data&quot;,
          &quot;provenance&quot;: &quot;fixture&quot;
        },
        &quot;fx_pair&quot;: [
          &quot;USDC&quot;,
          &quot;USDC&quot;
        ],
        &quot;fee_policy&quot;: {
          &quot;rate&quot;: &quot;0.0003&quot;,
          &quot;premium_cap&quot;: &quot;0.125&quot;,
          &quot;fee_currency&quot;: &quot;USDC&quot;,
          &quot;provenance&quot;: &quot;fixture&quot;
        },
        &quot;fee_known_ns&quot;: 0,
        &quot;metadata_known_ns&quot;: 0,
        &quot;timing&quot;: {
          &quot;max_age_ns&quot;: 100,
          &quot;minimum_source_age_ns&quot;: 0
        },
        &quot;source_identities&quot;: [],
        &quot;assumptions&quot;: [
          &quot;MATHEMATICAL_FIXTURE_NOT_MARKET_DATA&quot;
        ]
      }
    ],
    &quot;terminal_fee&quot;: {
      &quot;amount_report&quot;: &quot;0.001&quot;,
      &quot;known_ns&quot;: 0,
      &quot;source_ref&quot;: &quot;mathematical_total_terminal_fee_assumption&quot;
    },
    &quot;source_identities&quot;: [
      [
        &quot;fixture&quot;,
        &quot;mathematical_fixture_not_market_data&quot;
      ]
    ]
  },
  &quot;terminal_diagnostic&quot;: {
    &quot;request_id&quot;: &quot;single-long-call&quot;,
    &quot;input_hash&quot;: &quot;8ca9a0d59858734c617b255e3bd8e4299daeb0dbcc37809deb596e1b3da49f71&quot;,
    &quot;spec_hash&quot;: &quot;6bb544f3bccf4e56f85881a251420f89c289c90c3d3c7348ba4b3a2972614c3b&quot;,
    &quot;scenario_hash&quot;: &quot;3fb254e9b3873d87a8e84aa32518a60bd6fe080705549731da4ed4c4075253eb&quot;,
    &quot;model_version&quot;: &quot;single_paid_long_terminal.rust.v1&quot;,
    &quot;support&quot;: {
      &quot;input_validity&quot;: {
        &quot;status&quot;: &quot;VALID&quot;,
        &quot;reason&quot;: &quot;ADMITTED_TERMINAL_DIAGNOSTIC&quot;
      },
      &quot;model_support&quot;: {
        &quot;status&quot;: &quot;VALID&quot;,
        &quot;reason&quot;: &quot;SINGLE_LONG_TERMINAL_CONDITIONAL_MODEL&quot;
      },
      &quot;modeled_feasibility&quot;: {
        &quot;status&quot;: &quot;NOT_EVALUABLE&quot;,
        &quot;reason&quot;: &quot;FUNDING_NOT_ASSESSED&quot;
      },
      &quot;account_eligibility&quot;: {
        &quot;status&quot;: &quot;UNVERIFIED&quot;,
        &quot;reason&quot;: &quot;NO_ACCOUNT_EVIDENCE&quot;
      },
      &quot;execution_evidence&quot;: {
        &quot;status&quot;: &quot;UNVERIFIED&quot;,
        &quot;reason&quot;: &quot;NO_EXECUTION_EVIDENCE&quot;
      }
    },
    &quot;selected_quote_hash&quot;: &quot;6cdba9209bb38bae6d424b57f95f9ed3663080adefea45e80f28d4dd658e7ef9&quot;,
    &quot;cost&quot;: {
      &quot;status&quot;: {
        &quot;status&quot;: &quot;VALID&quot;,
        &quot;reason&quot;: &quot;DISPLAYED_COST_ONLY_NO_FILL&quot;
      },
      &quot;instrument_hash&quot;: &quot;6bb544f3bccf4e56f85881a251420f89c289c90c3d3c7348ba4b3a2972614c3b&quot;,
      &quot;book_hash&quot;: &quot;0000000000000000000000000000000000000000000000000000000000000000&quot;,
      &quot;side&quot;: &quot;BUY&quot;,
      &quot;quote_currency&quot;: &quot;USDC&quot;,
      &quot;report_currency&quot;: &quot;USDC&quot;,
      &quot;asof_ns&quot;: 10,
      &quot;quantity_native&quot;: &quot;0.01&quot;,
      &quot;premium_native&quot;: &quot;0.02&quot;,
      &quot;fee_native&quot;: &quot;0.0003&quot;,
      &quot;total_report_currency&quot;: &quot;0.0203&quot;,
      &quot;assumptions&quot;: [
        &quot;MATHEMATICAL_FIXTURE_NOT_MARKET_DATA&quot;
      ],
      &quot;input_hash&quot;: &quot;131ac18cc1aea51e6496bd4874ee7e7a74aefdd3ba3de41503e0c459f0e4f258&quot;
    },
    &quot;net_status&quot;: {
      &quot;status&quot;: &quot;VALID&quot;,
      &quot;reason&quot;: &quot;CONDITIONAL_NET_ONLY_NO_FUNDING_OR_FILL&quot;
    },
    &quot;outcomes&quot;: [
      {
        &quot;scenario_id&quot;: &quot;price-0&quot;,
        &quot;currency&quot;: &quot;USDC&quot;,
        &quot;gross_payoff&quot;: &quot;0&quot;,
        &quot;conditional_model_pnl&quot;: &quot;-0.0213&quot;,
        &quot;reason&quot;: &quot;CONDITIONAL_TERMINAL_PNL_NOT_ACTUAL&quot;
      },
      {
        &quot;scenario_id&quot;: &quot;price-90&quot;,
        &quot;currency&quot;: &quot;USDC&quot;,
        &quot;gross_payoff&quot;: &quot;0&quot;,
        &quot;conditional_model_pnl&quot;: &quot;-0.0213&quot;,
        &quot;reason&quot;: &quot;CONDITIONAL_TERMINAL_PNL_NOT_ACTUAL&quot;
      },
      {
        &quot;scenario_id&quot;: &quot;price-100&quot;,
        &quot;currency&quot;: &quot;USDC&quot;,
        &quot;gross_payoff&quot;: &quot;0&quot;,
        &quot;conditional_model_pnl&quot;: &quot;-0.0213&quot;,
        &quot;reason&quot;: &quot;CONDITIONAL_TERMINAL_PNL_NOT_ACTUAL&quot;
      },
      {
        &quot;scenario_id&quot;: &quot;price-110&quot;,
        &quot;currency&quot;: &quot;USDC&quot;,
        &quot;gross_payoff&quot;: &quot;0.1&quot;,
        &quot;conditional_model_pnl&quot;: &quot;0.0787&quot;,
        &quot;reason&quot;: &quot;CONDITIONAL_TERMINAL_PNL_NOT_ACTUAL&quot;
      }
    ],
    &quot;reasons&quot;: [
      &quot;CONDITIONAL_MATHEMATICS_NO_FUNDING_OR_FILL&quot;,
      &quot;NO_PROBABILITIES_OR_EV_MODEL&quot;
    ]
  }
}</pre>
