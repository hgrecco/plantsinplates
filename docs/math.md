# Mathematical Summary of Whole-Experiment Analysis

## 1. Scope and Data Structure

The analysis operates on repeated measurements of individual plants indexed by plate and well position. Let a plant trajectory be indexed by time points (t_k), with identifiers
\[
(\\text{plate}, \\text{row}, \\text{col}, \\text{genotype}).
\]
For each image, the pipeline extracts a root mask and then computes intensity and geometry descriptors according to a chosen measurement model.

## 2. Time Encoding and Temporal Differences

Dates are encoded as strings of the form
\[
\\texttt{days.HHmm}
\]
and converted to a continuous day variable:
\[
T_k = d_k + \\frac{h_k\\cdot 100 + m_k}{2400},
\]
where (d_k\\in\\mathbb{Z}\_{\\ge 0}), (h_k\\in{0,\\dots,23}), (m_k\\in{0,\\dots,59}).

Relative time is defined as
\[
\\Delta T_k^{(0)} = T_k - \\min_j T_j,
\]
and first differences (per plant) are
\[
\\delta T_k = \\Delta T_k^{(0)} - \\Delta T\_{k-1}^{(0)}.
\]

## 3. Root Segmentation and Object Selection

A binary mask (M) is obtained by thresholding and morphology:
\[
M = \\mathbf{1}[I>130],
\]
followed by removal of small objects and holes. If multiple connected components exist, only the largest-area component is retained for measurement.

## 4. Measurement Models (Analysis Options)

The system exposes three mutually exclusive measurement options.

### 4.1 `box` method

A rectangular ROI centered near the root tip is used.

1. Tip proxy: inferred from the mask extremity and local width estimate.
1. Optional offset: the ROI center can be shifted along the centroid-to-tip direction by
   \[
   \\mathbf{c}' = \\mathbf{c}_{\\text{tip}} + \\alpha,L_{\\text{box}},\\hat{\\mathbf{u}},
   \]
   where (\\alpha=\\texttt{box_offset}), (\\hat{\\mathbf{u}}) is the unit vector from mask centroid to tip, and (L\_{\\text{box}}) is the projected box extent along (\\hat{\\mathbf{u}}).

For ROI foreground (\\Omega_f) and background (\\Omega_b):
\[
\\mu_f=\\frac{1}{|\\Omega_f|}\\sum\_{p\\in\\Omega_f} I(p),\\quad
\\mu_b=\\frac{1}{|\\Omega_b|}\\sum\_{p\\in\\Omega_b} I(p).
\]
The exported scalar signal is
\[
S_k = \\mu_f - \\mu_b.
\]

### 4.2 `centerline` method

A centerline is estimated, smoothed by Savitzky-Golay, and sampled with perpendicular profiles.

For each ordered centerline point (q_i), intensities are sampled along its normal direction and summed:
\[
P_i = \\sum\_{u\\in\\mathcal{N}_i} I(u).
\]
The 1D profile (P_i) may be further smoothed (Savitzky-Golay). Let (i^\*=\\arg\\max_i P_i). A tip neighborhood is selected by Euclidean centerline distance:
\[
\\mathcal{I}_{\\text{tip}} = {i:|q_i-q\_{i^\*}|_2\\le L},
\]
where (L=\\texttt{length}). The scalar signal is
\[
S_k = \\sum_{i\\in\\mathcal{I}\_{\\text{tip}}} P_i.
\]

Additionally, profile regions (tip/middle/far and optional cap) are computed from a parametric fit (Section 5).

### 4.3 `centerline_gaussian` method

Same geometric framework as `centerline`, but each perpendicular cross-section is first fitted to a Gaussian:
\[
G(x)=A\\exp!\\left(-\\frac{(x-\\mu)^2}{2\\sigma^2}\\right)+b.
\]
The per-point centerline signal uses fitted peak amplitudes (A_i) (optionally smoothed), then applies the same tip-neighborhood integration as above:
\[
S_k = \\sum\_{i\\in\\mathcal{I}\_{\\text{tip}}} A_i.
\]
This option is more model-based and less sensitive to local baseline variation across each perpendicular profile.

## 5. Centerline Regional Decomposition

For centerline-based methods, the longitudinal intensity sequence is modeled as:
\[
f(x)=c + A_t\\exp!\\left(-\\frac{(x-\\mu_t)^2}{2\\sigma_t(x)^2}\\right)
\+ A_c\\exp!\\left(-\\frac{(x-\\mu_c)^2}{2\\sigma_c^2}\\right),
\]
with asymmetric tip width
\[
\\sigma_t(x)=
\\begin{cases}
\\sigma\_{t,L}, & x\\le \\mu_t,\\
\\sigma\_{t,R}, & x>\\mu_t.
\\end{cases}
\]

The fit is solved by bounded nonlinear least squares. Detected regions are then defined by support windows around fitted means (tip and candidate cap) and by backward windows of equal tip width (middle, far). For each region (R):
\[
\\text{mean}(R)=\\frac{1}{|R|}\\sum\_{i\\in R} P_i,
\\quad
\\text{integrated}(R)=\\sum\_{i\\in R} P_i,
\\quad
\\text{count}(R)=|R|.
\]

## 6. Whole-Experiment Derived Quantities

After per-image measurement, trajectories are sorted by (T_k) and first-order dynamics are computed per plant:
\[
\\delta S_k = S_k-S\_{k-1},\\quad
\\delta L_k = L_k-L\_{k-1},\\quad
\\delta T_k = T_k-T\_{k-1}.
\]
Rates (per day):
\[
R^{(L)}\_k = \\frac{\\delta L_k}{\\delta T_k},
\\qquad
R^{(S)}\_k = \\frac{\\delta S_k}{\\delta T_k}.
\]
A 2-point moving average for signal is also computed:
\[
\\bar{S}_k = \\frac{S_k+S_{k-1}}{2}.
\]

These quantities support plate-level and experiment-level comparative analyses across genotypes and time.

## 7. Parameters and Rationale

### Global analysis options

- `method` (\\in{\\texttt{box},\\texttt{centerline},\\texttt{centerline_gaussian}}): chooses the measurement model.

### `box` parameters

- `box_size` (pixels): ROI side length; larger values reduce variance but mix more heterogeneous tissue.
- `box_offset` (dimensionless): signed shift in box-size units along centroid-to-tip axis; useful to bias toward/away from apical region.

### Centerline geometry parameters

- `savgol_window`: smoothing window for centerline geometry; larger windows suppress digitization noise but can oversmooth curvature.
- `perpendicular_width`: half-domain for transverse sampling; controls cross-sectional support.
- `length`: longitudinal radius around profile maximum used for integration; larger values include more proximal tissue.

### Centerline intensity parameter

- `intensity_savgol_window`: smoothing window on the longitudinal intensity sequence. `0` disables smoothing; positive odd-effective window stabilizes noisy profiles while preserving trend.

## 8. Comparative Interpretation of Analysis Options

- `box`: simplest and fastest; directly interpretable as local foreground-minus-background contrast; most sensitive to ROI placement.
- `centerline`: geometry-aware integration around tip along root axis; more robust to orientation and local shape changes.
- `centerline_gaussian`: adds parametric denoising at each cross-section; best when profiles are approximately unimodal Gaussian with varying baseline.

In practice, method choice is a bias-variance tradeoff between interpretability (`box`) and structural robustness (`centerline`, `centerline_gaussian`).
