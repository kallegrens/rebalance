# Changelog

## [0.5.0](https://github.com/kallegrens/rebalance/compare/v0.4.0...v0.5.0) (2026-05-23)


### Features

* add band-aware monitoring and constrained rebalance planning ([9c432d9](https://github.com/kallegrens/rebalance/commit/9c432d9d3a744a57a34013355baed68533a48da3))
* add nordnet_stockholm courtage profile and asymmetric bands ([4096471](https://github.com/kallegrens/rebalance/commit/409647186cd18b4985a75b0e2831af003e1ad3fd))
* add support for leverage ([5ba6707](https://github.com/kallegrens/rebalance/commit/5ba6707bc0eab10845baf870059683baece86046))
* add support for notifications with apprise ([c1f13f7](https://github.com/kallegrens/rebalance/commit/c1f13f7a81d48e51a632257543c2803789c03386))
* **ci:** add container image build ([451c1b0](https://github.com/kallegrens/rebalance/commit/451c1b032e700c2444ebb3000c1a7d131f96c9b2))
* model Nordnet 0.25% FX conversion cost for non-SEK assets ([9441002](https://github.com/kallegrens/rebalance/commit/944100238197cf506ef55884df86fe270a08489e))
* replace SLSQP solver with MILP (cvxpy+HiGHS) and add fractional asset support ([adf2d1e](https://github.com/kallegrens/rebalance/commit/adf2d1e2222de00b5f47cb0775010331f17c2ab5))


### Bug Fixes

* **deps:** update python dependencies ([4cdc698](https://github.com/kallegrens/rebalance/commit/4cdc6986ab0a2c6e5f6674418bca5c891b0f83f4))
* **deps:** update python dependencies ([4f82e12](https://github.com/kallegrens/rebalance/commit/4f82e1278395eb2e234ab6c327add4eb79792b09))
* **release-please:** also bump the podman command in README ([91bda58](https://github.com/kallegrens/rebalance/commit/91bda583cd3e9aa316bcb3a70db2d3ff61a0135d))
* wake release-please to bump its PR ([76eaf3c](https://github.com/kallegrens/rebalance/commit/76eaf3cf6ff39738d9b18838364662d79ff6f0d4))

## [0.4.0](https://github.com/kallegrens/rebalance/compare/v0.3.1...v0.4.0) (2026-05-09)


### Features

* initial release ([243a06d](https://github.com/kallegrens/rebalance/commit/243a06d3545f5437615dd780af26299e2e4a6363))

## [0.3.1](https://github.com/kallegrens/rebalance/compare/v0.3.0...v0.3.1) (2026-05-09)


### Documentation

* replace README.rst with README.md (RST raw directives rejected by PyPI) ([e7da7e6](https://github.com/kallegrens/rebalance/commit/e7da7e67a8a74f434fdfb74935ba3263970511a8))

## [0.3.0](https://github.com/kallegrens/rebalance/compare/v0.2.0...v0.3.0) (2026-05-09)


### Features

* initial release ([2036616](https://github.com/kallegrens/rebalance/commit/203661664856c7fb233abd3dc578c0ba0cf00ee4))

## [0.2.0](https://github.com/kallegrens/rebalance/compare/v0.1.0...v0.2.0) (2026-05-09)


### Features

* add common_currency field and validator to PortfolioConfig ([fd8299b](https://github.com/kallegrens/rebalance/commit/fd8299b755d5563da59c90daa4dbe28e87c12772))
* add loguru logging with LOG_LEVEL env var and notification stub ([6247747](https://github.com/kallegrens/rebalance/commit/624774751381b81313ae9731f013871a88191446))


### Bug Fixes

* improved table printing ([122fdb3](https://github.com/kallegrens/rebalance/commit/122fdb39de672626aaa2a034534f7885eeb4068f))
* set yfinance session to None ([19956de](https://github.com/kallegrens/rebalance/commit/19956de279be41221f14918a6a8409c2e1e6f8c4))
* switch currency dependency ([197200e](https://github.com/kallegrens/rebalance/commit/197200ea1e841a939bc7b6ac16cd100db79824ae))
* TargetException check ([eac494d](https://github.com/kallegrens/rebalance/commit/eac494d7f0d827b12c0e3d2707a93fbb698ec9e4))


### Reverts

* remove Pipfile ([59f09f3](https://github.com/kallegrens/rebalance/commit/59f09f35b6d47d2ffbdc37be5a937e0ccfa142db))
