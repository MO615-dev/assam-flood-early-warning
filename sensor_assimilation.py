"""
sensor_assimilation.py
======================
Real-Time River Sensor Simulator with Data Assimilation
--------------------------------------------------------
Kopili River Basin, Assam — Continuous self-correcting water-level system

Architecture:
  ┌─────────────────────────────────────────────────────────┐
  │  discharge_2020.csv  (31 daily values)                  │
  │        │                                                 │
  │  Rating Curve Q→h   (h = 0.3 + (Q/45)^(1/1.8))        │
  │        │                                                 │
  │  Interpolate to 15-min steps  (2976 time steps)         │
  │        │                                                 │
  │  ┌─────┴──────────────────────────────────────────┐     │
  │  │  RiverSensorSimulator                          │     │
  │  │    true_stage(t) + sensor_noise + drift        │     │
  │  └─────────────────────────────────────────────── ┘     │
  │        │ z(t)  (noisy observation every 15 min)         │
  │        │                                                 │
  │  Model prediction: simple persistence / AR(1)           │
  │        │                                                 │
  │  ┌─────┴────────────────────────────────────────────┐   │
  │  │  1D Kalman Filter                                 │   │
  │  │    Predict → Update → corrected estimate          │   │
  │  └───────────────────────────────────────────────── ┘   │
  │        │                                                 │
  │  ┌─────┴────────────────────────────────────────────┐   │
  │  │  Nudging/Relaxation Corrector  (simpler baseline) │  │
  │  └───────────────────────────────────────────────── ┘   │
  │        │                                                 │
  │  matplotlib.animation  live rolling plot                 │
  └─────────────────────────────────────────────────────────┘

Classes:
  RiverSensorSimulator  – synthetic 15-min sensor readings
  KalmanFilter1D        – optimal linear state estimator
  NudgingCorrector      – relaxation-based data assimilation
  FloodAlertSystem      – threshold-based alert logic
  RealtimePlotter       – matplotlib.animation rolling window

Outputs:
  assimilation_result.csv   – full time series of all streams
  assimilation_final.png    – static summary plot
"""

import os
import sys
import warnings
import time

import numpy as np
import pandas as pd
import matplotlib
if "--headless" in sys.argv:
    matplotlib.use("Agg")
else:
    try:
        matplotlib.use("TkAgg")
    except Exception:
        matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import matplotlib.dates as mdates
from matplotlib.lines import Line2D

warnings.filterwarnings("ignore")

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# =============================================================================
# CONFIGURATION
# =============================================================================

DISCHARGE_CSV   = "discharge_2020.csv"
OUT_CSV         = "assimilation_result.csv"
OUT_PLOT_FINAL  = "assimilation_final.png"

# Rating curve parameters  Q = a * (h - h0)^b   [Kopili at Umrangso, approx]
# These are illustrative; real values come from CWPRS gauge calibration.
RATING_A  = 45.0    # coefficient
RATING_B  = 1.80    # exponent
RATING_H0 = 0.30    # zero-flow gauge datum (m)

# Sensor noise model
SENSOR_SIGMA  = 0.12   # standard deviation of random Gaussian noise (m)
SENSOR_DRIFT_RATE = 0.0008  # m per step – slow electronic drift
SENSOR_OUTLIER_PROB = 0.02  # probability of a gross error spike (2%)
SENSOR_OUTLIER_MAG  = 1.5   # magnitude of spike (m)

# Simple model error
MODEL_SIGMA = 0.20   # model prediction uncertainty std dev (m)

# Kalman filter tuning
KF_Q = 0.04    # process noise variance Q (model uncertainty^2)
KF_R = 0.0144  # observation noise variance R (SENSOR_SIGMA^2 = 0.12^2)

# Nudging (relaxation) tuning
NUDGE_ALPHA = 0.35  # 0=ignore obs, 1=jump straight to obs; 0.35 = moderate pull

# Flood alert thresholds (m) – illustrative for Kopili
ALERT_WARNING  = 5.5   # m above datum → Warning
ALERT_DANGER   = 7.0   # m above datum → Danger
ALERT_EXTREME  = 9.0   # m above datum → Extreme / evacuation

# Animation speed (ms between frames); set to 0 for fastest
ANIM_INTERVAL_MS = 80

# Rolling window shown in the live plot (number of 15-min steps)
ROLLING_WINDOW = 96    # 96 × 15 min = 24 hours

# =============================================================================
# STEP 1 – RATING CURVE  (Discharge → Stage)
# =============================================================================

def discharge_to_stage(q_m3s: float | np.ndarray,
                       a=RATING_A, b=RATING_B, h0=RATING_H0) -> float | np.ndarray:
    """
    Convert discharge (m³/s) to water stage (m) using power-law rating curve.

        Q = a * (h - h0)^b
    =>  h = h0 + (Q / a)^(1/b)

    This is a standard empirical relationship fitted to gauge data.
    For Kopili River we use illustrative but physically reasonable values.
    """
    q_safe = np.maximum(q_m3s, 0.0)
    h = h0 + (q_safe / a) ** (1.0 / b)
    return h


def stage_to_discharge(h_m: float | np.ndarray,
                       a=RATING_A, b=RATING_B, h0=RATING_H0) -> float | np.ndarray:
    """Inverse rating curve: stage (m) → discharge (m³/s)."""
    h_safe = np.maximum(h_m - h0, 0.0)
    return a * h_safe ** b


def load_and_interpolate(csv_path: str, steps_per_day: int = 96) -> pd.DataFrame:
    """
    Load daily discharge, convert to stage, interpolate to 15-min intervals.

    steps_per_day = 96  because 24 h * (60 min / 15 min) = 96 steps/day
    """
    print(f"  Loading {csv_path} ...")
    df = pd.read_csv(csv_path, parse_dates=["date"])
    df = df.sort_values("date").reset_index(drop=True)

    # Build daily time index at noon (representative of daily average)
    df["datetime"] = pd.to_datetime(df["date"]) + pd.Timedelta(hours=12)
    df["stage_m"]  = discharge_to_stage(df["total_discharge_m3s"].values)

    # Create 15-minute datetime index spanning the whole month
    t_start = df["datetime"].iloc[0]  - pd.Timedelta(hours=12)
    t_end   = df["datetime"].iloc[-1] + pd.Timedelta(hours=12)
    fine_index = pd.date_range(t_start, t_end, freq="15min")

    # Resample & cubic-spline interpolate
    daily_series = df.set_index("datetime")["stage_m"]
    fine_series = daily_series.reindex(
        daily_series.index.union(fine_index)
    ).interpolate(method="cubic").reindex(fine_index)

    # Cubic spline can produce NaN beyond the first/last anchor point.
    # Fill any remaining NaN forward then backward so the array is complete.
    fine_series = fine_series.ffill().bfill()

    result = pd.DataFrame({
        "datetime": fine_index,
        "true_stage_m": fine_series.values.clip(RATING_H0 + 0.01),
    })
    # Safety: replace any residual NaN with the series mean
    if result["true_stage_m"].isna().any():
        result["true_stage_m"].fillna(result["true_stage_m"].mean(), inplace=True)
    print(f"  Interpolated to {len(result)} steps @ 15-min resolution.")
    print(f"  Stage range: {result['true_stage_m'].min():.2f} – "
          f"{result['true_stage_m'].max():.2f} m")
    return result


# =============================================================================
# STEP 2 – RIVER SENSOR SIMULATOR
# =============================================================================

class RiverSensorSimulator:
    """
    Simulates a pressure-transducer water-level sensor at a river gauging station.

    Noise model (realistic):
    ------------------------
    z(t) = h_true(t)
           + N(0, sigma)          # Gaussian random noise (electronics, turbulence)
           + drift(t)             # slow electronic drift (temperature, fouling)
           + outlier(t)           # occasional gross errors (debris, comms dropout)

    Parameters
    ----------
    sigma       : std dev of random noise (m)
    drift_rate  : drift increment per step (m/step)
    outlier_p   : probability of a spike at any given step
    outlier_mag : magnitude of spike (m)
    seed        : random seed for reproducibility
    """

    def __init__(self,
                 sigma       = SENSOR_SIGMA,
                 drift_rate  = SENSOR_DRIFT_RATE,
                 outlier_p   = SENSOR_OUTLIER_PROB,
                 outlier_mag = SENSOR_OUTLIER_MAG,
                 seed        = 42):
        self.sigma       = sigma
        self.drift_rate  = drift_rate
        self.outlier_p   = outlier_p
        self.outlier_mag = outlier_mag
        self.rng         = np.random.default_rng(seed)
        self._drift      = 0.0         # accumulated drift (resets on maintenance)
        self._step       = 0
        self._maintenance_interval = 48  # reset drift every 12 hours

    def observe(self, true_stage: float) -> tuple[float, bool]:
        """
        Generate one 15-min sensor reading.

        Returns
        -------
        (observation, is_outlier)
        """
        # Slow drift accumulation
        self._drift += self.drift_rate * self.rng.choice([-1, 1])
        # Periodic maintenance resets drift
        if self._step % self._maintenance_interval == 0 and self._step > 0:
            self._drift *= 0.1   # technician corrects most of the drift

        # Gaussian noise
        noise = self.rng.normal(0.0, self.sigma)

        # Gross error / outlier
        is_outlier = self.rng.random() < self.outlier_p
        spike = (self.rng.choice([-1, 1]) * self.outlier_mag) if is_outlier else 0.0

        observation = true_stage + noise + self._drift + spike
        observation = max(observation, RATING_H0)   # physical lower bound

        self._step += 1
        return float(observation), is_outlier

    def batch_observe(self, true_stages: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Vectorised batch generation for pre-simulation."""
        obs, flags = [], []
        for h in true_stages:
            z, flag = self.observe(h)
            obs.append(z)
            flags.append(flag)
        return np.array(obs), np.array(flags)


# =============================================================================
# STEP 3 – SIMPLE MODEL (AR-1 PERSISTENCE)
# =============================================================================

class SimpleRiverModel:
    """
    Simplest possible open-loop forecast model: AR(1) persistence with decay.

    h_model(t+1) = alpha * h_model(t) + (1 - alpha) * h_mean + eps(t)

    This deliberately under-performs so data assimilation has room to improve.

    Parameters
    ----------
    alpha      : persistence coefficient (0.90 = slow decay towards climatology)
    h_mean     : climatological mean stage (m) — long-run attractor
    model_sigma: std dev of random model error per step
    """

    def __init__(self, h_init: float, h_mean: float,
                 alpha: float = 0.92,
                 model_sigma: float = MODEL_SIGMA,
                 seed: int = 99):
        self.alpha       = alpha
        self.h_mean      = h_mean
        self.sigma       = model_sigma
        self.h           = h_init
        self.rng         = np.random.default_rng(seed)

    def step(self) -> float:
        """Advance model by one 15-min step, return predicted stage."""
        self.h = (self.alpha * self.h
                  + (1 - self.alpha) * self.h_mean
                  + self.rng.normal(0, self.sigma))
        self.h = max(self.h, RATING_H0)
        return float(self.h)

    def set_state(self, h: float) -> None:
        """Externally reset model state (used after assimilation update)."""
        self.h = h


# =============================================================================
# STEP 4 – 1D KALMAN FILTER
# =============================================================================

class KalmanFilter1D:
    """
    Optimal 1-D linear Kalman Filter for scalar water-level estimation.

    State vector : x  = water stage h (m)           [scalar]
    State model  : x(t+1) = F*x(t) + w,  w ~ N(0, Q)
    Observation  : z(t) = H*x(t) + v,    v ~ N(0, R)

    For scalar system: F = 1, H = 1 (observe the state directly).

    Steps per time increment:
    ─────────────────────────
    PREDICT:
        x_pred = F * x_est                   (prior mean)
        P_pred = F * P * F + Q               (prior variance)

    UPDATE (when observation z arrives):
        K      = P_pred * H / (H * P_pred * H + R)   (Kalman gain 0→1)
        x_est  = x_pred + K * (z - H * x_pred)       (posterior mean)
        P      = (1 - K * H) * P_pred                (posterior variance)

    Kalman gain K interpretation:
        K ≈ 0  → trust model, ignore observation (R >> P_pred)
        K ≈ 1  → trust observation, ignore model (R << P_pred)

    Parameters
    ----------
    x0 : initial state estimate (m)
    P0 : initial error variance (m²)
    Q  : process noise variance (m²) — model uncertainty
    R  : observation noise variance (m²) — sensor uncertainty
    F  : state transition coefficient (default 1.0 = persistence)
    H  : observation matrix coefficient (default 1.0 = direct obs)
    """

    def __init__(self,
                 x0: float, P0: float = 1.0,
                 Q: float = KF_Q,
                 R: float = KF_R,
                 F: float = 1.0,
                 H: float = 1.0):
        self.x = x0       # state estimate
        self.P = P0       # error covariance (uncertainty)
        self.Q = Q        # process noise variance
        self.R = R        # observation noise variance
        self.F = F        # state transition
        self.H = H        # observation operator
        self.K = 0.0      # current Kalman gain (for logging)

    def predict(self) -> float:
        """Prediction step: propagate state and inflate uncertainty."""
        self.x = self.F * self.x
        self.P = self.F * self.P * self.F + self.Q
        return self.x

    def update(self, z: float) -> float:
        """
        Update step: correct prediction with observation.
        Returns posterior estimate.
        """
        S = self.H * self.P * self.H + self.R     # innovation covariance
        self.K = self.P * self.H / S              # Kalman gain
        innovation = z - self.H * self.x          # observation residual
        self.x = self.x + self.K * innovation     # posterior mean
        self.P = (1.0 - self.K * self.H) * self.P  # posterior variance
        self.x = max(self.x, RATING_H0)
        return self.x

    def step(self, z: float) -> tuple[float, float, float]:
        """Full predict+update cycle. Returns (estimate, Kalman_gain, uncertainty)."""
        self.predict()
        self.update(z)
        return self.x, self.K, self.P


# =============================================================================
# STEP 5 – NUDGING / RELAXATION CORRECTOR
# =============================================================================

class NudgingCorrector:
    """
    Simple relaxation (nudging) data assimilation.

    Also called Newtonian Relaxation — used widely in operational NWP
    as a cheap alternative to Kalman filtering.

    Formula:
        h_corrected(t) = h_model(t) + alpha * (z(t) - h_model(t))
                       = (1 - alpha) * h_model(t) + alpha * z(t)

    Interpretation:
        alpha = 0.0 → pure model (no correction)
        alpha = 0.5 → equal weight to model and obs
        alpha = 1.0 → jump directly to observation

    An adaptive variant adjusts alpha based on recent error history.

    Parameters
    ----------
    alpha         : base relaxation factor
    adaptive      : if True, adapt alpha based on rolling innovation RMS
    window        : window size for adaptive alpha estimation
    """

    def __init__(self, alpha: float = NUDGE_ALPHA,
                 adaptive: bool = True, window: int = 12):
        self.alpha_base  = alpha
        self.alpha       = alpha
        self.adaptive    = adaptive
        self.window      = window
        self._innovations = []   # recent (obs - model) differences

    def correct(self, h_model: float, z: float) -> tuple[float, float]:
        """
        Apply nudging correction.

        Returns (h_corrected, effective_alpha)
        """
        innovation = z - h_model
        self._innovations.append(innovation)
        if len(self._innovations) > self.window:
            self._innovations.pop(0)

        if self.adaptive and len(self._innovations) >= 4:
            # Adaptive alpha: increase when model–obs mismatch is large
            innov_rms = float(np.sqrt(np.mean(np.array(self._innovations) ** 2)))
            # Scale alpha between alpha_base and 0.7 based on RMS innovation
            self.alpha = float(np.clip(
                self.alpha_base * (1 + innov_rms / 0.5), 0.05, 0.70
            ))

        h_corr = h_model + self.alpha * innovation
        h_corr = max(h_corr, RATING_H0)
        return float(h_corr), float(self.alpha)


# =============================================================================
# STEP 6 – FLOOD ALERT SYSTEM
# =============================================================================

class FloodAlertSystem:
    """
    Threshold-based alert with hysteresis to avoid alert flickering.

    Returns alert level: 0=Normal, 1=Warning, 2=Danger, 3=Extreme
    """
    LEVELS = {0: "NORMAL", 1: "WARNING", 2: "DANGER", 3: "EXTREME"}
    COLORS = {0: "#22c55e", 1: "#facc15", 2: "#f97316", 3: "#ef4444"}

    def __init__(self, hysteresis: float = 0.15):
        self._level      = 0
        self.hysteresis  = hysteresis

    def check(self, stage: float) -> int:
        thresholds_up   = [ALERT_WARNING, ALERT_DANGER, ALERT_EXTREME]
        thresholds_down = [t - self.hysteresis for t in thresholds_up]

        # Escalate
        for lv, thr in enumerate(thresholds_up, 1):
            if stage >= thr and self._level < lv:
                self._level = lv
        # De-escalate with hysteresis
        for lv in range(self._level, 0, -1):
            if stage < thresholds_down[lv - 1]:
                self._level = lv - 1
                break
        return self._level


# =============================================================================
# STEP 7 – PRE-SIMULATE ALL TIME STEPS
# =============================================================================

def run_full_simulation(df: pd.DataFrame) -> pd.DataFrame:
    """
    Run all assimilation algorithms over the entire time series.
    Returns an augmented DataFrame with all output columns.
    """
    n = len(df)
    true_stages = df["true_stage_m"].values

    # Objects
    sensor    = RiverSensorSimulator(seed=42)
    h0        = true_stages[0]
    model     = SimpleRiverModel(h_init=h0, h_mean=float(np.mean(true_stages)))
    kf        = KalmanFilter1D(x0=h0, P0=0.5)
    nudge     = NudgingCorrector(adaptive=True)
    alerter   = FloodAlertSystem()

    # Pre-allocate arrays
    obs           = np.zeros(n)
    is_outlier    = np.zeros(n, dtype=bool)
    model_pred    = np.zeros(n)
    kf_est        = np.zeros(n)
    kf_gain       = np.zeros(n)
    kf_uncertainty= np.zeros(n)
    nudge_corr    = np.zeros(n)
    nudge_alpha   = np.zeros(n)
    alert_level   = np.zeros(n, dtype=int)

    print(f"  Simulating {n} steps ({n * 15 / 60:.1f} hours) ...")
    for i, h_true in enumerate(true_stages):
        # Guard: skip NaN truth values (shouldn't occur after ffill/bfill)
        if not np.isfinite(h_true):
            h_true = float(np.nanmean(true_stages))
        # --- Sensor observation ---
        z, outlier = sensor.observe(h_true)
        obs[i]        = z
        is_outlier[i] = outlier

        # --- Open-loop model prediction ---
        h_m = model.step()
        model_pred[i] = h_m

        # Outlier rejection: if spike is implausibly large, skip KF update
        # (simple innovation gate: reject if |z - model| > 3 * sqrt(R))
        innov_threshold = 3.0 * np.sqrt(KF_R + kf.P)
        if abs(z - kf.x) > innov_threshold:
            # Treat as missing observation: predict only
            kf.predict()
            kf_est[i]        = kf.x
            nudge_corr[i], nudge_alpha[i] = nudge.correct(h_m, kf.x)
        else:
            # Normal assimilation
            est, gain, unc   = kf.step(z)
            kf_est[i]        = est
            kf_gain[i]       = gain
            kf_uncertainty[i]= unc
            nudge_corr[i], nudge_alpha[i] = nudge.correct(h_m, z)

        # Feed KF estimate back into model (state update)
        model.set_state(kf_est[i])

        alert_level[i] = alerter.check(kf_est[i])

    df = df.copy()
    df["observed_m"]      = obs
    df["is_outlier"]      = is_outlier
    df["model_pred_m"]    = model_pred
    df["kf_estimate_m"]   = kf_est
    df["kf_gain"]         = kf_gain
    df["kf_uncertainty"]  = kf_uncertainty
    df["nudge_corr_m"]    = nudge_corr
    df["nudge_alpha"]     = nudge_alpha
    df["alert_level"]     = alert_level

    # Performance metrics — use nanmean/nanstd to handle any residual NaN
    rmse_model = float(np.sqrt(np.nanmean((model_pred - true_stages) ** 2)))
    rmse_kf    = float(np.sqrt(np.nanmean((kf_est    - true_stages) ** 2)))
    rmse_nudge = float(np.sqrt(np.nanmean((nudge_corr - true_stages) ** 2)))
    mae_model  = float(np.nanmean(np.abs(model_pred - true_stages)))
    mae_kf     = float(np.nanmean(np.abs(kf_est     - true_stages)))

    print(f"\n  ---- PERFORMANCE METRICS (vs truth) ----")
    print(f"  {'':20s}  {'RMSE':>8}  {'MAE':>8}")
    print(f"  {'Open-loop model':20s}  {rmse_model:8.4f}  {mae_model:8.4f} m")
    print(f"  {'Kalman filter':20s}  {rmse_kf:8.4f}  {mae_kf:8.4f} m")
    print(f"  {'Nudging (adaptive)':20s}  {rmse_nudge:8.4f}  {'--':>8}")
    if rmse_model > 0:
        kf_skill = 100 * (1 - rmse_kf / rmse_model)
        print(f"  Kalman skill score: {kf_skill:.1f}% RMSE reduction vs open-loop model")

    return df


# =============================================================================
# STEP 8 – REAL-TIME ANIMATED PLOT
# =============================================================================

class RealtimePlotter:
    """
    matplotlib.animation-based rolling 24-hour live display.

    Shows:
    - True stage (ground truth, dashed)
    - Noisy sensor observations (scatter)
    - Open-loop model prediction
    - Kalman filter estimate + uncertainty band
    - Nudging estimate
    - Kalman gain panel
    - Flood alert coloured background
    """

    ALERT_COLORS = {0: "#14532d22", 1: "#78350f44",
                    2: "#7c2d1244", 3: "#7f1d1d66"}

    def __init__(self, df: pd.DataFrame, window: int = ROLLING_WINDOW):
        self.df     = df
        self.window = window
        self.n      = len(df)
        self._setup_figure()

    def _setup_figure(self):
        self.fig = plt.figure(figsize=(14, 9), facecolor="#0f172a")
        gs = self.fig.add_gridspec(3, 1, height_ratios=[4, 1, 1], hspace=0.08)

        self.ax_main = self.fig.add_subplot(gs[0])
        self.ax_gain = self.fig.add_subplot(gs[1], sharex=self.ax_main)
        self.ax_alert= self.fig.add_subplot(gs[2], sharex=self.ax_main)

        for ax in (self.ax_main, self.ax_gain, self.ax_alert):
            ax.set_facecolor("#1e293b")
            ax.tick_params(colors="white", labelsize=8)
            ax.spines[:].set_color("#334155")
            ax.yaxis.label.set_color("white")

        plt.setp(self.ax_main.get_xticklabels(), visible=False)
        plt.setp(self.ax_gain.get_xticklabels(), visible=False)

        # --- Main panel lines ---
        dates = self.df["datetime"].values

        self.ln_true, = self.ax_main.plot(
            [], [], "--", color="#94a3b8", lw=1.5, label="True stage", zorder=5)
        self.sc_obs   = self.ax_main.scatter(
            [], [], s=10, color="#38bdf8", alpha=0.4, label="Observation", zorder=4)
        self.sc_out   = self.ax_main.scatter(
            [], [], s=40, marker="x", color="#f43f5e", label="Outlier rejected", zorder=6)
        self.ln_model, = self.ax_main.plot(
            [], [], color="#fb923c", lw=1.2, alpha=0.7, label="Open-loop model", zorder=3)
        self.ln_kf,   = self.ax_main.plot(
            [], [], color="#4ade80", lw=2.0, label="Kalman estimate", zorder=7)
        self.fill_kf  = self.ax_main.fill_between(
            [], [], [], alpha=0.15, color="#4ade80", label="KF uncertainty (1σ)")
        self.ln_nudge,= self.ax_main.plot(
            [], [], color="#c084fc", lw=1.5, ls="-.",
            label="Nudging estimate", zorder=6)

        # Flood threshold lines
        for lvl, thr, col, lbl in [
            (1, ALERT_WARNING, "#facc15", f"Warning ({ALERT_WARNING}m)"),
            (2, ALERT_DANGER,  "#f97316", f"Danger ({ALERT_DANGER}m)"),
            (3, ALERT_EXTREME, "#ef4444", f"Extreme ({ALERT_EXTREME}m)"),
        ]:
            self.ax_main.axhline(thr, color=col, lw=0.8, ls=":", alpha=0.6, label=lbl)

        self.ax_main.set_ylabel("Water Stage (m)", color="white", fontsize=10)
        self.ax_main.legend(loc="upper left", facecolor="#1e293b",
                            edgecolor="#334155", labelcolor="white",
                            fontsize=7, ncol=3)
        self.title = self.ax_main.set_title("", color="white", fontsize=11, pad=8)

        # --- Kalman gain panel ---
        self.ln_kgain, = self.ax_gain.plot([], [], color="#818cf8", lw=1.5)
        self.ax_gain.set_ylabel("K gain", color="#818cf8", fontsize=8)
        self.ax_gain.set_ylim(0, 1)
        self.ax_gain.axhline(0.5, color="#334155", lw=0.5, ls="--")

        # --- Alert level panel ---
        self.ln_alert, = self.ax_alert.step([], [], color="#f43f5e", lw=1.5, where="post")
        self.ax_alert.set_ylabel("Alert", color="#f43f5e", fontsize=8)
        self.ax_alert.set_ylim(-0.2, 3.5)
        self.ax_alert.set_yticks([0, 1, 2, 3])
        self.ax_alert.set_yticklabels(["OK", "Warn", "Danger", "XTREME"],
                                       fontsize=7, color="white")
        self.ax_alert.xaxis.set_major_formatter(mdates.DateFormatter("%d %b\n%H:%M"))
        self.ax_alert.xaxis.set_major_locator(mdates.HourLocator(interval=6))
        self.ax_alert.set_xlabel("Date / Time (UTC)", color="white", fontsize=9)

        self.fig.suptitle(
            "Kopili River — Real-Time Data Assimilation  |  July 2020",
            color="white", fontsize=12, fontweight="bold", y=0.98
        )

    def _update(self, frame: int):
        """Called by FuncAnimation for each frame (one 15-min step ahead)."""
        i_end   = min(frame + 1, self.n)
        i_start = max(0, i_end - self.window)
        sl      = slice(i_start, i_end)

        d  = self.df.iloc[sl]
        dt = d["datetime"].values
        if len(dt) == 0:
            return

        # True stage
        self.ln_true.set_data(dt, d["true_stage_m"])
        # All observations
        self.sc_obs.set_offsets(
            np.column_stack([mdates.date2num(pd.to_datetime(dt)),
                             d["observed_m"]]))
        # Outlier markers
        out = d[d["is_outlier"]]
        if not out.empty:
            self.sc_out.set_offsets(
                np.column_stack([
                    mdates.date2num(pd.to_datetime(out["datetime"])),
                    out["observed_m"]
                ]))
        # Model
        self.ln_model.set_data(dt, d["model_pred_m"])
        # KF estimate
        self.ln_kf.set_data(dt, d["kf_estimate_m"])
        # KF uncertainty band
        [p.remove() for p in self.ax_main.collections
         if hasattr(p, "_label") and "KF unc" in str(p._label)]
        kf_upper = d["kf_estimate_m"] + np.sqrt(d["kf_uncertainty"])
        kf_lower = d["kf_estimate_m"] - np.sqrt(d["kf_uncertainty"])
        self.ax_main.fill_between(dt, kf_lower, kf_upper,
                                  alpha=0.15, color="#4ade80")
        # Nudging
        self.ln_nudge.set_data(dt, d["nudge_corr_m"])
        # Kalman gain
        self.ln_kgain.set_data(dt, d["kf_gain"])
        # Alert
        self.ln_alert.set_data(dt, d["alert_level"])

        # Dynamic axis limits — use nanmin/nanmax to skip any NaN, then
        # guard against empty or all-NaN windows
        y_vals = np.concatenate([
            d["true_stage_m"].values,
            d["kf_estimate_m"].values,
            d["model_pred_m"].values
        ])
        finite_vals = y_vals[np.isfinite(y_vals)]
        if len(finite_vals) == 0:
            finite_vals = np.array([RATING_H0, RATING_H0 + 1.0])
        ymin = max(RATING_H0, float(np.min(finite_vals)) - 0.5)
        ymax = float(np.max(finite_vals)) + 0.8
        if ymin >= ymax:
            ymax = ymin + 1.0
        self.ax_main.set_ylim(ymin, ymax)
        self.ax_main.set_xlim(dt[0], dt[-1])

        # Alert background colour
        al = int(d["alert_level"].iloc[-1])
        self.ax_main.set_facecolor(self.ALERT_COLORS[al])

        # Title with live status
        current_h  = float(d["kf_estimate_m"].iloc[-1])
        alert_name = FloodAlertSystem.LEVELS[al]
        alert_col  = FloodAlertSystem.COLORS[al]
        ts_str     = pd.to_datetime(dt[-1]).strftime("%d %b %H:%M")
        self.title.set_text(
            f"[{ts_str}]   KF Stage: {current_h:.2f} m   |   "
            f"Alert: {alert_name}   |   "
            f"Showing last {(i_end - i_start) * 15 // 60}h"
        )
        self.title.set_color(alert_col)

        return (self.ln_true, self.ln_model, self.ln_kf,
                self.ln_nudge, self.ln_kgain, self.ln_alert)

    def run_animation(self) -> None:
        """Launch the live FuncAnimation. Blocks until window is closed."""
        ani = animation.FuncAnimation(
            self.fig,
            self._update,
            frames=range(0, self.n, 1),
            interval=ANIM_INTERVAL_MS,
            blit=False,
            repeat=False,
        )
        plt.tight_layout()
        try:
            plt.show()
        except Exception:
            pass
        return ani


# =============================================================================
# STEP 9 – STATIC SUMMARY PLOT
# =============================================================================

def save_static_plot(df: pd.DataFrame, out_path: str) -> None:
    """Full-month static summary of all streams + RMSE annotations."""
    print(f"  Generating static summary -> {out_path}")
    dt = df["datetime"]

    fig, axes = plt.subplots(4, 1, figsize=(15, 12), sharex=True,
                             gridspec_kw={"height_ratios": [4, 1, 1, 1],
                                          "hspace": 0.07})
    fig.patch.set_facecolor("#0f172a")
    for ax in axes:
        ax.set_facecolor("#1e293b")
        ax.tick_params(colors="white", labelsize=8)
        ax.spines[:].set_color("#334155")

    ax_h, ax_gain, ax_alp, ax_al = axes

    # Flood zones
    ax_h.axhspan(ALERT_WARNING, ALERT_DANGER,  alpha=0.08, color="#facc15")
    ax_h.axhspan(ALERT_DANGER,  ALERT_EXTREME, alpha=0.08, color="#f97316")
    ax_h.axhspan(ALERT_EXTREME, 20,            alpha=0.10, color="#ef4444")

    ax_h.plot(dt, df["true_stage_m"],   "--", color="#94a3b8", lw=1.2, label="Truth")
    ax_h.scatter(dt[~df["is_outlier"]], df["observed_m"][~df["is_outlier"]],
                 s=3, color="#38bdf8", alpha=0.3, label="Observation")
    ax_h.scatter(dt[df["is_outlier"]],  df["observed_m"][df["is_outlier"]],
                 s=25, marker="x", color="#f43f5e", label="Outlier")
    ax_h.plot(dt, df["model_pred_m"],   color="#fb923c", lw=1.0, alpha=0.7, label="Open-loop model")
    ax_h.plot(dt, df["nudge_corr_m"],   color="#c084fc", lw=1.2, ls="-.", label="Nudging")
    ax_h.plot(dt, df["kf_estimate_m"],  color="#4ade80", lw=1.6, label="Kalman filter")
    kf_upper = df["kf_estimate_m"] + np.sqrt(df["kf_uncertainty"])
    kf_lower = df["kf_estimate_m"] - np.sqrt(df["kf_uncertainty"])
    ax_h.fill_between(dt, kf_lower, kf_upper, alpha=0.18, color="#4ade80")

    # RMSE annotations
    rmse_model = np.sqrt(np.mean((df["model_pred_m"]  - df["true_stage_m"])**2))
    rmse_kf    = np.sqrt(np.mean((df["kf_estimate_m"] - df["true_stage_m"])**2))
    rmse_nudge = np.sqrt(np.mean((df["nudge_corr_m"]  - df["true_stage_m"])**2))
    ax_h.text(0.99, 0.97,
              f"RMSE  Model:{rmse_model:.3f}m  |  Nudge:{rmse_nudge:.3f}m  |  Kalman:{rmse_kf:.3f}m",
              transform=ax_h.transAxes, ha="right", va="top",
              color="white", fontsize=8, bbox=dict(fc="#0f172a", ec="#334155", pad=3))

    ax_h.set_ylabel("Water Stage (m)", color="white")
    ax_h.legend(loc="upper left", facecolor="#1e293b", edgecolor="#334155",
                labelcolor="white", fontsize=7, ncol=4)
    ax_h.set_title("Kopili River — Data Assimilation Summary | July 2020",
                   color="white", fontsize=12, fontweight="bold")

    ax_gain.plot(dt, df["kf_gain"], color="#818cf8", lw=1.0)
    ax_gain.set_ylabel("Kalman\nGain K", color="#818cf8", fontsize=8)
    ax_gain.set_ylim(0, 1)

    ax_alp.plot(dt, df["nudge_alpha"], color="#f472b6", lw=1.0)
    ax_alp.set_ylabel("Nudge\nalpha", color="#f472b6", fontsize=8)

    ax_al.fill_between(dt, df["alert_level"], step="post",
                       alpha=0.7, color="#f43f5e")
    ax_al.set_ylabel("Alert\nLevel", color="#f43f5e", fontsize=8)
    ax_al.set_yticks([0, 1, 2, 3])
    ax_al.set_yticklabels(["OK", "Warn", "Danger", "XTREME"], fontsize=7, color="white")
    ax_al.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax_al.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    plt.xticks(color="white")

    plt.tight_layout()
    plt.savefig(out_path, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    print(f"  [SAVED] -> {out_path}")
    if matplotlib.get_backend().lower() != "agg":
        plt.show()


# =============================================================================
# MAIN
# =============================================================================

def main():
    print("\n" + "=" * 62)
    print("  River Sensor Simulator + Data Assimilation")
    print("  Kopili Basin | July 2020")
    print("=" * 62)

    # --- STEP 1: Load & interpolate discharge -> stage ---
    print("\n--- STEP 1: Load & Interpolate Discharge -> Stage ---")
    if not os.path.exists(DISCHARGE_CSV):
        raise FileNotFoundError(
            f"{DISCHARGE_CSV} not found. Run rainfall_runoff.py first."
        )
    df = load_and_interpolate(DISCHARGE_CSV)

    # --- STEP 2: Run full simulation ---
    print("\n--- STEP 2: Running Simulation ---")
    df = run_full_simulation(df)

    # --- STEP 3: Save CSV ---
    print(f"\n--- STEP 3: Saving -> {OUT_CSV} ---")
    df.to_csv(OUT_CSV, index=False, float_format="%.4f")
    print(f"  Saved {len(df)} rows × {len(df.columns)} columns")

    # --- STEP 4: Static summary plot ---
    print(f"\n--- STEP 4: Static Summary Plot ---")
    save_static_plot(df, OUT_PLOT_FINAL)

    # --- STEP 5: Live animated rolling plot ---
    if "--headless" not in sys.argv and matplotlib.get_backend().lower() != "agg":
        print(f"\n--- STEP 5: Live Animation (close window to exit) ---")
        print(f"  Rolling window: {ROLLING_WINDOW * 15 // 60}h  |  "
              f"Animation speed: {ANIM_INTERVAL_MS}ms/frame")
        print(f"  Tip: close the matplotlib window to end the animation.")
        try:
            plotter = RealtimePlotter(df, window=ROLLING_WINDOW)
            plotter.run_animation()
        except Exception as exc:
            print(f"  [INFO] Animation failed ({exc}). Static plot saved instead.")
    else:
        print("\n--- STEP 5: Headless mode: skipping live animation ---")

    print("\n" + "=" * 62)
    print("  DONE — Outputs:")
    print(f"    {OUT_CSV}")
    print(f"    {OUT_PLOT_FINAL}")
    print("=" * 62)


if __name__ == "__main__":
    main()
