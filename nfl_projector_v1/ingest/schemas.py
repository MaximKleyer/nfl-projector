"""Schemas for every FPD CSV the model consumes.

This is the single source of truth: if FPD changes a column name, this is
the only file you need to edit.

Each schema describes:
- name: identifier for this report
- granularity: 'player' or 'team' (defense/offense)
- weekly: True if this report has weekly granularity, False for season-only
- columns: list of (raw_name, normalized_name, dtype) tuples for the columns
           we use. Columns not in this list are ignored.
- section_layout: for "wide" CSVs that repeat columns across sections
                  (man/zone, depth buckets, alignment splits), this defines
                  the section labels in order.

The readers module uses these schemas to parse each CSV into a clean
long-format DataFrame.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class RequiredColumn:
    raw: str           # column name as it appears in the FPD CSV
    canonical: str     # column name we use internally
    dtype: str = "float64"  # 'int64', 'float64', or 'string'


@dataclass
class ReportSchema:
    name: str
    filename_pattern: str  # regex / glob for matching files
    granularity: Literal["player", "team_off", "team_def"]
    weekly: bool
    columns: list[RequiredColumn] = field(default_factory=list)
    section_layout: list[str] | None = None  # for wide CSVs only
    section_columns: list[RequiredColumn] | None = None  # cols repeated per section
    notes: str = ""


# ---------------------------------------------------------------------------
# Standard identifier columns (present in nearly every player file)
# ---------------------------------------------------------------------------
_PLAYER_ID_COLS = [
    RequiredColumn("Name", "player_name", "string"),
    RequiredColumn("Team", "team", "string"),
    RequiredColumn("POS", "position", "string"),
    RequiredColumn("G", "games_played", "int64"),
    RequiredColumn("Season", "season", "int64"),
]

_TEAM_ID_COLS = [
    RequiredColumn("Name", "team_full_name", "string"),
    RequiredColumn("G", "games_played", "int64"),
    RequiredColumn("Season", "season", "int64"),
    RequiredColumn("Location", "team_location", "string"),
    RequiredColumn("Team Name", "team_name", "string"),
]


# ---------------------------------------------------------------------------
# Advanced Passing — player-level
# ---------------------------------------------------------------------------
ADVANCED_PASSING_PLAYER = ReportSchema(
    name="advanced_passing_player",
    filename_pattern="advanced_passing_player.csv",
    granularity="player",
    weekly=True,
    columns=_PLAYER_ID_COLS + [
        RequiredColumn("DB", "dropbacks", "int64"),
        RequiredColumn("ATT", "pass_att", "int64"),
        RequiredColumn("CMP", "completions", "int64"),
        RequiredColumn("CMP %", "cmp_pct", "float64"),
        RequiredColumn("YDS", "pass_yds", "int64"),
        RequiredColumn("YDS/G", "pass_yds_per_g", "float64"),
        RequiredColumn("YPA", "ypa", "float64"),
        RequiredColumn("TD", "pass_td", "int64"),
        RequiredColumn("INT", "ints", "int64"),
        RequiredColumn("RATE", "passer_rating", "float64"),
        RequiredColumn("SACK", "sacks_taken", "int64"),
        RequiredColumn("SACK %", "sack_pct", "float64"),
        RequiredColumn("ANY/A", "any_per_a", "float64"),
        RequiredColumn("SCRM", "scrambles", "int64"),
        RequiredColumn("CPOE", "cpoe", "float64"),
        RequiredColumn("aDOT", "adot", "float64"),
        RequiredColumn("AY", "air_yards", "int64"),
        RequiredColumn("Deep Throw %", "deep_throw_pct", "float64"),
        RequiredColumn("YAC %", "yac_pct", "float64"),
        RequiredColumn("ADJ CMP %", "adj_cmp_pct", "float64"),
        RequiredColumn("1Read %", "first_read_pct", "float64"),
        RequiredColumn("ACC %", "acc_pct", "float64"),
        RequiredColumn("CATCH %", "catch_pct", "float64"),
        RequiredColumn("OFF %", "off_target_pct", "float64"),
        RequiredColumn("HERO %", "hero_pct", "float64"),
        RequiredColumn("TWT %", "twt_pct", "float64"),
        RequiredColumn("EZATT", "ez_att", "int64"),
        RequiredColumn("DROP %", "drop_pct", "float64"),
        RequiredColumn("TTT", "time_to_throw", "float64"),
        RequiredColumn("TTP", "time_to_pressure", "float64"),
        RequiredColumn("PRESS %", "pressure_pct", "float64"),
        RequiredColumn("PrROE", "press_rate_over_exp", "float64"),
        RequiredColumn("CHK %", "checkdown_pct", "float64"),
        RequiredColumn("RPO %", "rpo_pct", "float64"),
        RequiredColumn("FP/G", "fp_per_g", "float64"),
        RequiredColumn("FP", "total_fp", "float64"),
    ],
    notes="One row per QB per week. TWT % and HERO % are charted-quality stats unique to FPD.",
)

ADVANCED_PASSING_DEFENSE = ReportSchema(
    name="advanced_passing_def",
    filename_pattern="advanced_passing_def.csv",
    granularity="team_def",
    weekly=True,
    columns=_TEAM_ID_COLS + [
        RequiredColumn("DB", "def_dropbacks", "int64"),
        RequiredColumn("ATT", "def_pass_att", "int64"),
        RequiredColumn("CMP %", "def_cmp_pct", "float64"),
        RequiredColumn("YDS", "def_pass_yds", "int64"),
        RequiredColumn("YPA", "def_ypa", "float64"),
        RequiredColumn("TD", "def_pass_td", "int64"),
        RequiredColumn("INT", "def_ints", "int64"),
        RequiredColumn("RATE", "def_passer_rating", "float64"),
        RequiredColumn("SACK", "def_sacks", "int64"),
        RequiredColumn("SACK %", "def_sack_pct", "float64"),
        RequiredColumn("ANY/A", "def_any_per_a", "float64"),
        RequiredColumn("PRESS %", "def_pressure_pct", "float64"),
        RequiredColumn("aDOT", "def_adot", "float64"),
        RequiredColumn("Deep Throw %", "def_deep_throw_pct", "float64"),
    ],
    notes="One row per defense per week. Used for opponent-quality features.",
)


# ---------------------------------------------------------------------------
# Advanced Receiving — player-level
# ---------------------------------------------------------------------------
ADVANCED_RECEIVING_PLAYER = ReportSchema(
    name="advanced_receiving_player",
    filename_pattern="advanced_receiving_player.csv",
    granularity="player",
    weekly=True,
    columns=_PLAYER_ID_COLS + [
        RequiredColumn("RTE", "routes_run", "int64"),
        RequiredColumn("RTE %", "route_pct", "float64"),
        RequiredColumn("aDOT", "adot", "float64"),
        RequiredColumn("AY", "air_yards", "int64"),
        RequiredColumn("AY Share", "air_yards_share", "float64"),
        RequiredColumn("TGT", "targets", "int64"),
        RequiredColumn("TGT/G", "targets_per_g", "float64"),
        RequiredColumn("TGT %", "target_share", "float64"),
        RequiredColumn("TPRR", "tprr", "float64"),
        RequiredColumn("REC", "receptions", "int64"),
        RequiredColumn("CR %", "catch_rate", "float64"),
        RequiredColumn("YDS", "rec_yds", "int64"),
        RequiredColumn("RecYDS/G", "rec_yds_per_g", "float64"),
        RequiredColumn("TM YDS %", "team_yds_share", "float64"),
        RequiredColumn("YPRR", "yprr", "float64"),
        RequiredColumn("YPT", "ypt", "float64"),
        RequiredColumn("YPR", "ypr", "float64"),
        RequiredColumn("YAC", "yac", "int64"),
        RequiredColumn("YACO/REC", "yaco_per_rec", "float64"),
        RequiredColumn("TD", "rec_td", "int64"),
        RequiredColumn("TM TD %", "team_td_share", "float64"),
        RequiredColumn("i20 TGT", "i20_targets", "int64"),
        RequiredColumn("EZTGT", "ez_targets", "int64"),
        RequiredColumn("EZTD", "ez_td", "int64"),
        RequiredColumn("DP TGT", "deep_targets", "int64"),
        RequiredColumn("1READ", "first_read_targets", "int64"),
        RequiredColumn("1READ %", "first_read_target_share", "float64"),
        RequiredColumn("MTF", "mtf_after_catch", "int64"),
        RequiredColumn("MTF/REC", "mtf_per_rec", "float64"),
        RequiredColumn("1D/RR", "first_downs_per_rr", "float64"),
        RequiredColumn("DRP %", "drop_pct", "float64"),
        RequiredColumn("CTGT", "catchable_targets", "int64"),
        RequiredColumn("CTGT %", "catchable_target_pct", "float64"),
        RequiredColumn("DESIGN %", "designed_target_pct", "float64"),
        RequiredColumn("CC %", "contested_catch_pct", "float64"),
        RequiredColumn("THREAT", "threat_score", "float64"),
        RequiredColumn("YPTOE", "yards_per_target_oe", "float64"),
        RequiredColumn("WIDE RTE %", "wide_route_pct", "float64"),
        RequiredColumn("SLOT RTE %", "slot_route_pct", "float64"),
        RequiredColumn("INLINE RTE %", "inline_route_pct", "float64"),
        RequiredColumn("BACK RTE %", "back_route_pct", "float64"),
        RequiredColumn("FP/RR", "fp_per_rr", "float64"),
        RequiredColumn("FP/G", "fp_per_g", "float64"),
        RequiredColumn("FP", "total_fp", "float64"),
        RequiredColumn("XFP", "xfp", "float64"),
        RequiredColumn("XFP/G", "xfp_per_g", "float64"),
        RequiredColumn("XFP/RR", "xfp_per_rr", "float64"),
        RequiredColumn("RecXFP", "rec_xfp", "float64"),
    ],
    notes=(
        "One row per pass-catcher per week. The xFP family is the foundation "
        "of Block A. CTGT %, YPTOE, 1READ %, and AY Share are FPD-specific edges."
    ),
)


# ---------------------------------------------------------------------------
# Advanced Rushing — player-level (wide format with concept splits)
# ---------------------------------------------------------------------------
ADVANCED_RECEIVING_DEFENSE = ReportSchema(
    name="advanced_receiving_def",
    filename_pattern="advanced_receiving_def.csv",
    granularity="team_def",
    weekly=True,
    columns=_TEAM_ID_COLS + [
        # Volume / dropbacks faced
        RequiredColumn("RTE", "def_routes_faced", "int64"),
        RequiredColumn("aDOT", "def_adot_allowed", "float64"),
        RequiredColumn("AY", "def_air_yards_allowed", "int64"),
        RequiredColumn("TGT", "def_targets_faced", "int64"),
        RequiredColumn("TPRR", "def_tprr_allowed", "float64"),
        # Outcomes
        RequiredColumn("REC", "def_receptions_allowed", "int64"),
        RequiredColumn("CR %", "def_catch_rate_allowed", "float64"),
        RequiredColumn("YDS", "def_rec_yds_allowed", "int64"),
        RequiredColumn("RecYDS/G", "def_rec_yds_per_g", "float64"),
        RequiredColumn("YPRR", "def_yprr_allowed", "float64"),
        RequiredColumn("YPT", "def_ypt_allowed", "float64"),
        RequiredColumn("YPR", "def_ypr_allowed", "float64"),
        RequiredColumn("YAC", "def_yac_allowed", "int64"),
        RequiredColumn("YAC/REC", "def_yac_per_rec", "float64"),
        RequiredColumn("YACO", "def_yaco_allowed", "int64"),
        RequiredColumn("YACO/REC", "def_yaco_per_rec", "float64"),
        RequiredColumn("TD", "def_rec_td_allowed", "int64"),
        RequiredColumn("i20", "def_i20_targets_faced", "int64"),
        RequiredColumn("EZTGT", "def_ez_targets_faced", "int64"),
        RequiredColumn("EZTD", "def_ez_td_allowed", "int64"),
        # Tackling / processing
        RequiredColumn("MTF", "def_mtf_allowed_after_catch", "int64"),
        RequiredColumn("MTF/REC", "def_mtf_per_rec", "float64"),
        RequiredColumn("1D", "def_first_downs_allowed", "int64"),
        RequiredColumn("1D/RR", "def_first_downs_per_rr", "float64"),
        # Drop / catchability (defensive impact on receivers)
        RequiredColumn("DRP", "def_drops_forced", "int64"),
        RequiredColumn("DRP %", "def_drop_pct", "float64"),
        RequiredColumn("CTGT", "def_catchable_targets_faced", "int64"),
        RequiredColumn("CTGT %", "def_catchable_target_pct", "float64"),
        RequiredColumn("DESIGN", "def_designed_targets_faced", "int64"),
        RequiredColumn("DESIGN %", "def_designed_target_pct", "float64"),
        RequiredColumn("CT", "def_contested_targets", "int64"),
        RequiredColumn("CC", "def_contested_catches_allowed", "int64"),
        RequiredColumn("CC %", "def_contested_catch_pct_allowed", "float64"),
        RequiredColumn("HERO", "def_hero_allowed", "int64"),
        RequiredColumn("RATE", "def_passer_rating_when_targeted", "float64"),
        RequiredColumn("1READ", "def_first_read_targets_faced", "int64"),
        RequiredColumn("1READ %", "def_first_read_target_pct", "float64"),
        RequiredColumn("YPTOE", "def_yards_per_target_oe_allowed", "float64"),
        # Alignment splits — these are TARGET % faced by alignment, which
        # tells us where defenses are vulnerable
        RequiredColumn("WIDE TGT %", "def_wide_target_share_faced", "float64"),
        RequiredColumn("SLOT TGT %", "def_slot_target_share_faced", "float64"),
        RequiredColumn("INLINE TGT %", "def_inline_target_share_faced", "float64"),
        RequiredColumn("BACK TGT %", "def_back_target_share_faced", "float64"),
    ],
    notes=(
        "Defensive receiving stats — what defenses allow at the receiver "
        "level. The most predictive columns for projection: def_yprr_allowed "
        "(secondary play quality), def_catch_rate_allowed (coverage tightness), "
        "def_yards_per_target_oe_allowed (process-based defense quality), and "
        "the alignment target shares (where the defense is most exploited)."
    ),
)



ADVANCED_RUSHING_PLAYER = ReportSchema(
    name="advanced_rushing_player",
    filename_pattern="advanced_rushing_player.csv",
    granularity="player",
    weekly=True,
    columns=_PLAYER_ID_COLS + [
        # Total / overall rushing stats (first occurrence of each)
        RequiredColumn("ATT", "rush_att", "int64"),
        RequiredColumn("YDS", "rush_yds", "int64"),
        RequiredColumn("RuYDS/G", "rush_yds_per_g", "float64"),
        RequiredColumn("YPC", "ypc", "float64"),
        RequiredColumn("TD", "rush_td", "int64"),
        RequiredColumn("FUM", "fumbles", "int64"),
        RequiredColumn("EXP RUN %", "explosive_run_pct", "float64"),
        RequiredColumn("i5 %", "inside5_share", "float64"),
        RequiredColumn("TD RATE", "rush_td_rate", "float64"),
        RequiredColumn("Success %", "rush_success_pct", "float64"),
        RequiredColumn("STUFF %", "stuff_pct", "float64"),
        RequiredColumn("MTF", "rush_mtf", "int64"),
        RequiredColumn("MTF/ATT", "rush_mtf_per_att", "float64"),
        RequiredColumn("YACO/ATT", "yaco_per_att", "float64"),
        RequiredColumn("YBCO/ATT", "ybco_per_att", "float64"),
        RequiredColumn("FP/G", "fp_per_g", "float64"),
        RequiredColumn("FP", "total_fp", "float64"),
        RequiredColumn("XFP", "xfp", "float64"),
        RequiredColumn("XFP/G", "xfp_per_g", "float64"),
    ],
    section_layout=["zone", "gap"],  # cols after the totals are zone, then gap
    section_columns=[
        RequiredColumn("ATT", "att", "int64"),
        RequiredColumn("ATT %", "att_share", "float64"),
        RequiredColumn("YDS", "yds", "int64"),
        RequiredColumn("TD", "td", "int64"),
        RequiredColumn("YPC", "ypc", "float64"),
        RequiredColumn("Success %", "success_pct", "float64"),
    ],
    notes=(
        "Two sections after the totals: zone-concept rushing (cols 26-31) and "
        "gap-concept rushing (cols 32-37). Section columns become "
        "{zone|gap}_{att|yds|...}."
    ),
)


ADVANCED_RUSHING_DEFENSE = ReportSchema(
    name="advanced_rushing_def",
    filename_pattern="advanced_rushing_def.csv",
    granularity="team_def",
    weekly=True,
    columns=_TEAM_ID_COLS + [
        RequiredColumn("ATT", "def_rush_att", "int64"),
        RequiredColumn("YDS", "def_rush_yds", "int64"),
        RequiredColumn("RuYDS/G", "def_rush_yds_per_g", "float64"),
        RequiredColumn("YPC", "def_ypc", "float64"),
        RequiredColumn("TD", "def_rush_td", "int64"),
        RequiredColumn("FUM", "def_fumbles_forced", "int64"),
        RequiredColumn("1D", "def_rush_first_downs", "int64"),
        RequiredColumn("EXP RUN %", "def_explosive_run_pct", "float64"),
        RequiredColumn("EXP YDS", "def_explosive_yds", "int64"),
        RequiredColumn("EXP YDS %", "def_explosive_yds_pct", "float64"),
        RequiredColumn("i5 ATT", "def_i5_att", "int64"),
        RequiredColumn("TD RATE", "def_rush_td_rate", "float64"),
        RequiredColumn("Success %", "def_rush_success_pct", "float64"),
        RequiredColumn("STUFF %", "def_stuff_pct", "float64"),
        RequiredColumn("MTF", "def_mtf_allowed", "int64"),
        RequiredColumn("MTF/ATT", "def_mtf_per_att", "float64"),
        RequiredColumn("YACO", "def_yaco_allowed", "int64"),
        RequiredColumn("YACO/ATT", "def_yaco_per_att", "float64"),
        RequiredColumn("YACO %", "def_yaco_pct", "float64"),
        RequiredColumn("YBCO/ATT", "def_ybco_per_att", "float64"),
    ],
    section_layout=["zone", "gap"],
    section_columns=[
        RequiredColumn("ATT", "att", "int64"),
        RequiredColumn("ATT %", "att_share", "float64"),
        RequiredColumn("YDS", "yds", "int64"),
        RequiredColumn("TD", "td", "int64"),
        RequiredColumn("YPC", "ypc", "float64"),
        RequiredColumn("Success %", "success_pct", "float64"),
    ],
    notes=(
        "Defensive rushing, with totals followed by zone/gap concept splits. "
        "def_stuff_pct, def_yaco_per_att (tackling quality), and "
        "def_explosive_run_pct are the highest-signal columns for evaluating "
        "rush defense quality."
    ),
)


# ---------------------------------------------------------------------------
# Snaps — player-level (wide format, 6 sections)
# ---------------------------------------------------------------------------
SNAPS = ReportSchema(
    name="snaps",
    filename_pattern="snaps.csv",
    granularity="player",
    weekly=True,
    columns=_PLAYER_ID_COLS,
    # Section order DECODED from the real 2021-2025 files (the raw FPD header
    # labels were wrong): TOTAL, then a RUSH/PASS play-type split (rush + pass
    # = total), then three nested red-zone tiers smallest->largest. gl/i10/rz
    # are inferred tier names (goal-line, inside-10, red-zone); the exact
    # yard-lines are unconfirmed but the ordering is certain. Fix here if FPD
    # changes ordering.
    section_layout=["total", "rush", "pass", "gl", "i10", "rz"],
    section_columns=[
        RequiredColumn("Snaps", "snaps", "int64"),
        RequiredColumn("TM Snaps", "tm_snaps", "int64"),
        RequiredColumn("Snap %", "snap_pct", "float64"),
    ],
    notes=(
        "Six sections of (Snaps, TM Snaps, Snap %): total offensive snaps; then "
        "run-play and pass-play snaps (rush + pass = total); then three nested "
        "red-zone tiers (gl, i10, rz = smallest to largest). total_snap_pct is "
        "the headline snap share; the rush/pass split separates early-down from "
        "pass-down RBs. Verified against the 2021-2025 files - the raw FPD column "
        "labels did NOT match, so do not trust the header names."
    ),
)


# ---------------------------------------------------------------------------
# Routes Run — player-level (wide format, alignment splits)
# ---------------------------------------------------------------------------
ROUTES_RUN = ReportSchema(
    name="routes_run",
    filename_pattern="routes_run.csv",
    granularity="player",
    weekly=True,
    columns=_PLAYER_ID_COLS + [
        RequiredColumn("RTE", "total_routes", "int64"),
        RequiredColumn("TGT", "total_targets", "int64"),
        RequiredColumn("TPRR", "total_tprr", "float64"),
        RequiredColumn("RTE %", "total_route_pct", "float64"),
        RequiredColumn("YPRR", "total_yprr", "float64"),
    ],
    # After the totals there are 4 alignment sections: WIDE, SLOT, INLINE, BACK
    section_layout=["wide", "slot", "inline", "back"],
    section_columns=[
        RequiredColumn("RTE", "routes", "int64"),
        # First column of each section is also the alignment-rate label
        # (WIDE RTE %, SLOT RTE %, etc.). Section reader will assign canonically.
        RequiredColumn("__ALIGN_PCT__", "alignment_share", "float64"),
        RequiredColumn("TM RTE %", "tm_route_share", "float64"),
        RequiredColumn("TGT", "targets", "int64"),
        RequiredColumn("TPRR", "tprr", "float64"),
        RequiredColumn("RTE %", "route_pct_within_align", "float64"),
        RequiredColumn("YPRR", "yprr", "float64"),
    ],
    notes=(
        "Per-alignment YPRR — important for matching receiver alignment to "
        "opponent FPA-vs-alignment. The __ALIGN_PCT__ marker is replaced with "
        "the actual column name (WIDE RTE %, SLOT RTE %, etc.) at read time."
    ),
)


# ---------------------------------------------------------------------------
# Receiving Man vs Zone — player-level (wide format, coverage splits)
# ---------------------------------------------------------------------------
MAN_VS_ZONE = ReportSchema(
    name="man_vs_zone",
    filename_pattern="man_vs_zone.csv",
    granularity="player",
    weekly=True,
    columns=_PLAYER_ID_COLS,
    # FPD's actual section order in the file (from inspecting the CSV):
    # TOTAL, MAN, ZONE, plus two coverage-shell sections (1-HI and 2-HI).
    section_layout=["total", "man", "zone", "shell_1hi", "shell_2hi"],
    section_columns=[
        RequiredColumn("RTE", "routes", "int64"),
        RequiredColumn("TPRR", "tprr", "float64"),
        RequiredColumn("YPRR", "yprr", "float64"),
        RequiredColumn("FP/RR", "fp_per_rr", "float64"),
    ],
    notes=(
        "Receiver performance split by coverage scheme. Drives Block B's "
        "coverage-conditional efficiency layer."
    ),
)


# ---------------------------------------------------------------------------
# Passing Depth of Target — player-level (wide format, depth buckets)
# ---------------------------------------------------------------------------
PASSING_DEPTH = ReportSchema(
    name="passing_depth",
    filename_pattern="passing_depth.csv",
    granularity="player",
    weekly=True,
    columns=_PLAYER_ID_COLS,
    # Sections: TOTAL, BTLOS (behind LOS), SHORT (0-9), INTERMEDIATE (10-19), DEEP (20+).
    section_layout=["total", "btlos", "short", "intermediate", "deep"],
    section_columns=[
        RequiredColumn("ATT", "att", "int64"),
        RequiredColumn("CMP", "cmp", "int64"),
        RequiredColumn("YDS", "yds", "int64"),
        RequiredColumn("TD", "td", "int64"),
        RequiredColumn("INT", "ints", "int64"),
        RequiredColumn("RATE", "rating", "float64"),
        RequiredColumn("CATCH %", "catch_pct", "float64"),
        RequiredColumn("HERO", "hero", "int64"),
        RequiredColumn("TWT", "twt", "int64"),
    ],
    notes=(
        "QB stats by depth bucket. TD rate per attempt differs ~4-5x between "
        "short and deep, so this matters for QB projection accuracy."
    ),
)


# ---------------------------------------------------------------------------
# Run/Pass Report — team-level (wide format, situation splits)
# ---------------------------------------------------------------------------
RUN_PASS_REPORT = ReportSchema(
    name="run_pass_report",
    filename_pattern="run_pass_report.csv",
    granularity="team_off",
    weekly=True,
    columns=_TEAM_ID_COLS,
    # FPD's report has 13 sections of (SNAPS, PASS, RUSH, PASS %, RUSH %).
    # The exact section labels need to be confirmed against FPD's docs;
    # below is our best inference. If wrong, the impact is on which team-pace
    # priors we use, not on whether the data ingests.
    section_layout=[
        "total", "first_half", "second_half", "q1", "q2", "q3", "q4",
        "neutral_script", "trailing", "leading", "first_down", "second_down",
        "third_down",
    ],
    section_columns=[
        RequiredColumn("SNAPS", "snaps", "int64"),
        RequiredColumn("PASS", "pass_plays", "int64"),
        RequiredColumn("RUSH", "rush_plays", "int64"),
        RequiredColumn("PASS %", "pass_pct", "float64"),
        RequiredColumn("RUSH %", "rush_pct", "float64"),
    ],
    notes=(
        "Team pass/rush splits by situation. 'neutral_script' pass % is the "
        "most predictive for projecting future pass attempts. Section labels "
        "below are inferred — verify against FPD docs and update if needed."
    ),
)


# ---------------------------------------------------------------------------
# Fantasy Points Allowed — team-defense, one file per position
# ---------------------------------------------------------------------------
def _fpa_schema(pos: str) -> ReportSchema:
    return ReportSchema(
        name=f"fpa_{pos.lower()}",
        filename_pattern=f"fpa_{pos.lower()}.csv",
        granularity="team_def",
        weekly=True,
        columns=_TEAM_ID_COLS + [
            RequiredColumn("FP/G", f"fpa_{pos.lower()}_per_g", "float64"),
            RequiredColumn("FP", f"fpa_{pos.lower()}_total", "float64"),
        ],
        notes=f"Fantasy points allowed to {pos}s. Drives Block C matchup multiplier.",
    )


FPA_QB = _fpa_schema("QB")
FPA_RB = _fpa_schema("RB")
FPA_WR = _fpa_schema("WR")
FPA_TE = _fpa_schema("TE")


# ---------------------------------------------------------------------------
# Coverage Matrix — team-level (season-aggregate, one file each off + def)
# ---------------------------------------------------------------------------
COVERAGE_MATRIX_OFF = ReportSchema(
    name="coverage_matrix_off",
    filename_pattern="coverage_matrix_off.csv",
    granularity="team_off",
    weekly=False,
    columns=_TEAM_ID_COLS + [
        RequiredColumn("DB", "off_dropbacks", "int64"),
        RequiredColumn("MAN %", "off_man_rate_faced", "float64"),
        RequiredColumn("ZONE %", "off_zone_rate_faced", "float64"),
        RequiredColumn("1-HI/MOF C %", "off_1hi_rate_faced", "float64"),
        RequiredColumn("2-HI/MOF O %", "off_2hi_rate_faced", "float64"),
        RequiredColumn("COVER 0 %", "off_cov0_faced", "float64"),
        RequiredColumn("COVER 1 %", "off_cov1_faced", "float64"),
        RequiredColumn("COVER 2 %", "off_cov2_faced", "float64"),
        RequiredColumn("COVER 3 %", "off_cov3_faced", "float64"),
        RequiredColumn("COVER 4 %", "off_cov4_faced", "float64"),
        RequiredColumn("COVER 6 %", "off_cov6_faced", "float64"),
    ],
    notes="Season-aggregate. Pull once per season, not per week.",
)

COVERAGE_MATRIX_DEF = ReportSchema(
    name="coverage_matrix_def",
    filename_pattern="coverage_matrix_def.csv",
    granularity="team_def",
    weekly=False,
    columns=_TEAM_ID_COLS + [
        RequiredColumn("DB", "def_dropbacks", "int64"),
        RequiredColumn("MAN %", "def_man_rate", "float64"),
        RequiredColumn("ZONE %", "def_zone_rate", "float64"),
        RequiredColumn("1-HI/MOF C %", "def_1hi_rate", "float64"),
        RequiredColumn("2-HI/MOF O %", "def_2hi_rate", "float64"),
        RequiredColumn("COVER 0 %", "def_cov0", "float64"),
        RequiredColumn("COVER 1 %", "def_cov1", "float64"),
        RequiredColumn("COVER 2 %", "def_cov2", "float64"),
        RequiredColumn("COVER 3 %", "def_cov3", "float64"),
        RequiredColumn("COVER 4 %", "def_cov4", "float64"),
        RequiredColumn("COVER 6 %", "def_cov6", "float64"),
    ],
    notes="Season-aggregate. The defense's coverage tendency feeds Block C.",
)


# ---------------------------------------------------------------------------
# Line Matchups (DiBenedetto OL/DL grading) — team-level, season-aggregate
# ---------------------------------------------------------------------------
LINE_MATCHUPS = ReportSchema(
    name="line_matchups",
    filename_pattern="line_matchups.csv",
    granularity="team_off",
    weekly=False,
    columns=_TEAM_ID_COLS + [
        RequiredColumn("RUSH GRADE", "ol_rush_grade", "float64"),
        RequiredColumn("PASS GRADE", "ol_pass_grade", "float64"),
        RequiredColumn("ADJ YBC/ATT", "ol_adj_ybc_per_att", "float64"),
        RequiredColumn("PRESS %", "ol_press_pct_allowed", "float64"),
        RequiredColumn("PrROE", "ol_proe_allowed", "float64"),
    ],
    notes=(
        "Season-aggregate OL grades (DiBenedetto). Critical for QB sack "
        "projection and RB efficiency. Pull once per season."
    ),
)


# ---------------------------------------------------------------------------
# External (non-FPD) data sources
# ---------------------------------------------------------------------------
SCHEDULE = ReportSchema(
    name="schedule",
    filename_pattern="schedule.csv",
    granularity="team_off",  # game-level, but indexed by team
    weekly=True,
    columns=[
        RequiredColumn("season", "season", "int64"),
        RequiredColumn("week", "week", "int64"),
        RequiredColumn("game_id", "game_id", "string"),
        RequiredColumn("home_team", "home_team", "string"),
        RequiredColumn("away_team", "away_team", "string"),
        RequiredColumn("home_score", "home_score", "int64"),
        RequiredColumn("away_score", "away_score", "int64"),
        RequiredColumn("kickoff_dt", "kickoff_dt", "string"),
        RequiredColumn("dome", "dome", "int64"),
        RequiredColumn("surface", "surface", "string"),
        RequiredColumn("weather_temp", "weather_temp", "float64"),
        RequiredColumn("weather_wind", "weather_wind", "float64"),
    ],
    notes=(
        "External, NOT from FPD. Fetched from nflverse via "
        "scripts/fetch_external_data.py. Required to know who played whom."
    ),
)

VEGAS = ReportSchema(
    name="vegas",
    filename_pattern="vegas.csv",
    granularity="team_off",
    weekly=True,
    columns=[
        RequiredColumn("season", "season", "int64"),
        RequiredColumn("week", "week", "int64"),
        RequiredColumn("game_id", "game_id", "string"),
        RequiredColumn("spread_close", "spread_close", "float64"),
        RequiredColumn("total_close", "total_close", "float64"),
        RequiredColumn("moneyline_home", "moneyline_home", "float64"),
        RequiredColumn("moneyline_away", "moneyline_away", "float64"),
    ],
    notes="External. Closing lines from nflverse.",
)

INJURIES = ReportSchema(
    name="injuries",
    filename_pattern="injuries.csv",
    granularity="player",
    weekly=True,
    columns=[
        RequiredColumn("season", "season", "int64"),
        RequiredColumn("week", "week", "int64"),
        RequiredColumn("team", "team", "string"),
        RequiredColumn("gsis_id", "gsis_id", "string"),
        RequiredColumn("player_name", "player_name", "string"),
        RequiredColumn("position", "position", "string"),
        RequiredColumn("report_status", "report_status", "string"),
        RequiredColumn("practice_status", "practice_status", "string"),
        RequiredColumn("injury_primary", "injury_primary", "string"),
        RequiredColumn("injury_secondary", "injury_secondary", "string"),
        RequiredColumn("date_modified", "date_modified", "string"),
    ],
    notes=(
        "External. NFL injury reports from nflverse. Status values are "
        "'Questionable' (Q), 'Doubtful' (D), 'Out' (O), and 'IR' (long-term). "
        "Practice status: DNP / Limited / Full. Players without a "
        "report_status are excluded by the fetch script (no news = healthy)."
    ),
)


KICKING = ReportSchema(
    name="kicking",
    filename_pattern="kicking.csv",
    granularity="team_off",  # one row per team-game (FGs already aggregated)
    weekly=True,
    columns=[
        RequiredColumn("season", "season", "int64"),
        RequiredColumn("week", "week", "int64"),
        RequiredColumn("team", "team", "string"),
        RequiredColumn("fg_made", "fg_made", "float64"),
        RequiredColumn("fg_att", "fg_att", "float64"),
    ],
    notes=(
        "External. Per-team-game field goals from nflverse stats_player weekly "
        "kicking, aggregated across kickers (scripts/fetch_kicking.py). Feeds "
        "the per-team FG rate in team.py (DESIGN.md §11 #5)."
    ),
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
REPORTS: dict[str, ReportSchema] = {
    s.name: s for s in [
        ADVANCED_PASSING_PLAYER, ADVANCED_PASSING_DEFENSE,
        ADVANCED_RECEIVING_PLAYER, ADVANCED_RECEIVING_DEFENSE,
        ADVANCED_RUSHING_PLAYER, ADVANCED_RUSHING_DEFENSE,
        SNAPS, ROUTES_RUN, MAN_VS_ZONE, PASSING_DEPTH, RUN_PASS_REPORT,
        FPA_QB, FPA_RB, FPA_WR, FPA_TE,
        COVERAGE_MATRIX_OFF, COVERAGE_MATRIX_DEF, LINE_MATCHUPS,
        SCHEDULE, VEGAS, INJURIES, KICKING,
    ]
}


def list_reports() -> None:
    """Pretty-print every registered report and its purpose. CLI helper."""
    print(f"{'Report':40s}  {'Granularity':12s}  {'Weekly':7s}  Notes")
    print("-" * 100)
    for name, schema in REPORTS.items():
        weekly = "yes" if schema.weekly else "season"
        notes = (schema.notes or "").split(".")[0][:60]
        print(f"{name:40s}  {schema.granularity:12s}  {weekly:7s}  {notes}")
