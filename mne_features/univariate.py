# Author: Jean-Baptiste Schiratti <jean.baptiste.schiratti@gmail.com>
#         Alexandre Gramfort <alexandre.gramfort@inria.fr>
# License: BSD 3 clause


from functools import partial
from math import sqrt, log, floor

import numpy as np
import pywt
from scipy import stats, signal
from scipy.ndimage import convolve1d

from .mock_numba import nb
from .utils import power_spectrum, embed, filt


def get_univariate_funcs(sfreq):
    """ Returns a dictionary of univariate feature functions. For each feature
    function, the corresponding key in the dictionary is an alias for the
    function.

    Parameters
    ----------
    sfreq : float
        Sampling rate of the data.

    Returns
    -------
    univariate_funcs : dict
    """
    univariate_funcs = dict()
    univariate_funcs['mean'] = compute_mean
    univariate_funcs['variance'] = compute_variance
    univariate_funcs['std'] = compute_std
    univariate_funcs['ptp_amplitude'] = compute_ptp
    univariate_funcs['skewness'] = compute_skewness
    univariate_funcs['kurtosis'] = compute_kurtosis
    univariate_funcs['hurst_exp'] = compute_hurst_exponent
    univariate_funcs['decorr_time'] = partial(compute_decorr_time, sfreq)
    univariate_funcs['hjorth_mobility_spect'] = partial(
        compute_spect_hjorth_mobility, sfreq)
    univariate_funcs['hjorth_complexity_spect'] = partial(
        compute_spect_hjorth_complexity, sfreq)
    univariate_funcs['app_entropy'] = compute_app_entropy
    univariate_funcs['samp_entropy'] = compute_samp_entropy
    univariate_funcs['hjorth_mobility'] = compute_hjorth_mobility
    univariate_funcs['hjorth_complexity'] = compute_hjorth_complexity
    univariate_funcs['higuchi_fd'] = compute_higuchi_fd
    univariate_funcs['katz_fd'] = compute_katz_fd
    univariate_funcs['pow_freq_bands'] = partial(
        compute_power_spectrum_freq_bands, sfreq)
    univariate_funcs['zero_cross'] = compute_zero_crossings
    univariate_funcs['line_len'] = compute_line_length
    univariate_funcs['spect_entropy'] = partial(compute_spect_entropy, sfreq)
    univariate_funcs['svd_entropy'] = compute_svd_entropy
    univariate_funcs['svd_fisher_info'] = compute_svd_fisher_info
    univariate_funcs['spect_edge_freq'] = partial(compute_spect_edge_freq,
                                                  sfreq)
    univariate_funcs['wavelet_coef_energy'] = compute_wavelet_coef_energy
    return univariate_funcs


def _unbiased_autocorr(x):
    """ Unbiased autocorrelation.

    Parameters
    ----------
    x : ndarray, shape (n_times,)

    Returns
    -------
    ndarray, shape (2 * n_times + 1,)
    """
    m = x.shape[0] - 1
    lags = np.arange(-m, m + 1)
    s = np.add(m, - np.abs(lags))
    s[np.where(s <= 0)] = 1
    autocorr = signal.fftconvolve(x, x[::-1], mode='full')
    autocorr /= s
    return autocorr


@nb.jit([nb.float64(nb.float64[:], nb.float64[:]),
         nb.float32(nb.float32[:], nb.float32[:])], nopython=True)
def _slope_lstsq(x, y):
    """ Utility function which returns the slope of the linear
    regression between x and y.

    Parameters
    ----------
    x : ndarray, shape (n_times,)

    y : ndarray, shape (n_times,)

    Returns
    -------
    float
    """
    n_times = x.shape[0]
    sx2 = 0
    sx = 0
    sy = 0
    sxy = 0
    for j in range(n_times):
        sx2 += x[j] ** 2
        sx += x[j]
        sxy += x[j] * y[j]
        sy += y[j]
    den = n_times * sx2 - (sx ** 2)
    num = n_times * sxy - sx * sy
    return num / den


@nb.jit([nb.float64[:](nb.float64[:]), nb.float32[:](nb.float32[:])],
        nopython=True)
def _accumulate_std(x):
    r = np.zeros((x.shape[0],), dtype=x.dtype)
    for j in range(1, x.shape[0]):
        m = 0
        for k in range(j + 1):
            m += x[k]
        m /= (j + 1)
        s = 0
        for k in range(j + 1):
            s += (x[k] - m) ** 2
        s /= j
        r[j] = sqrt(s)
    return r


def compute_mean(data):
    """ Mean of the data (per channel).

    Parameters
    ----------
    data : ndarray, shape (n_channels, n_times)

    Returns
    -------
    output : ndarray, shape (n_channels,)
    """
    return np.mean(data, axis=-1)


def compute_variance(data):
    """ Variance of the data (per channel).

     Parameters
     ----------
     data : shape (n_channels, n_times)

     Returns
     -------
     output : ndarray, shape (n_channels,)
     """
    return np.var(data, axis=-1, ddof=1)


def compute_std(data):
    """ Standard deviation of the data.

    Parameters
    ----------
    data : shape (n_channels, n_times)

    Returns
    -------
    output : ndarray, shape (n_channels)
    """
    return np.std(data, axis=-1, ddof=1)


def compute_ptp(data):
    """ Peak-to-peak (PTP) amplitude of the data (per channel).

    Parameters
    ----------
    data : ndarray, shape (n_channels, n_times)

    Returns
    -------
    output : ndarray, shape (n_channels,)
    """
    return np.ptp(data, axis=-1)


def compute_skewness(data):
    """ Skewness of the data (per channel).

    Parameters
    ----------
    data : ndarray, shape (n_channels, n_times)

    Returns
    -------
    output : ndarray, shape (n_channels,)
    """

    ndim = data.ndim
    return stats.skew(data, axis=ndim - 1)


def compute_kurtosis(data):
    """ Kurtosis of the data (per channel).

    Parameters
    ----------
    data : ndarray, shape (n_channels, n_times)

    Returns
    -------
    output : ndarray, shape (n_channels,)
    """

    ndim = data.ndim
    return stats.kurtosis(data, axis=ndim - 1, fisher=False)


def compute_hurst_exponent(data):
    """ Hurst exponent [1, 2] of the data (per channel).

    Parameters
    ----------
    data : ndarray, shape (n_channels, n_times)

    Returns
    -------
    output : ndarray, shape (n_channels,)

    References
    ----------
    .. [1] Devarajan, K. et al. (2014). EEG-Based Epilepsy Detection and
           Prediction. International Journal of Engineering and Technology,
           6(3), 212.

    .. [2] https://en.wikipedia.org/wiki/Hurst_exponent
    """
    n_channels = data.shape[0]
    hurst_exponent = np.empty((n_channels,))
    for j in range(n_channels):
        m = np.mean(data[j, :])
        y = data[j, :] - m
        z = np.cumsum(y)
        r = (np.maximum.accumulate(z) - np.minimum.accumulate(z))[1:]
        s = _accumulate_std(data[j, :])[1:]
        s[np.where(s == 0)] = 1e-12  # avoid dividing by 0
        y_reg = np.log(r / s)
        x_reg = np.log(np.arange(1, y_reg.shape[0] + 1))
        hurst_exponent[j] = _slope_lstsq(x_reg, y_reg)
    return hurst_exponent.ravel()


@nb.jit([nb.float64[:](nb.float64[:, :]), nb.float32[:](nb.float32[:, :])],
        nopython=True)
def compute_app_entropy(data):
    """ Approximate Entropy (AppEn, per channel) [1].

    Parameters
    ----------
    data : shape (n_channels, n_times)

    Returns
    -------
    output : ndarray, shape (n_channels)

    References
    ----------
    .. [1] Teixeira, C. A. et al. (2011). EPILAB: A software package for
           studies on the prediction of epileptic seizures. Journal of
           Neuroscience Methods, 200(2), 257-271.
    """
    n_channels, n_times = data.shape
    appen = np.empty((n_channels,), dtype=data.dtype)
    for t in range(n_channels):
        s = 0
        for j in range(n_times):
            s += data[t, j] ** 2
        s /= (n_times - 1)
        rs = sqrt(s)
        r = 0.25 * rs
        p = 0.
        a = 0.
        b = 0.
        for i in range(n_times - 2):
            for j in range((i + 1), (n_times - 3)):
                d1 = abs(data[t, i] - data[t, j])
                d2 = abs(data[t, i + 1] - data[t, j + 1])
                d3 = abs(data[t, i + 2] - data[t, j + 2])
                if d1 >= d2:
                    da = d1
                else:
                    da = d2
                if da < r:
                    a += 1
                    if d3 < r:
                        b += 1
            if (a > 0) and (b > 0):
                pi = float(b) / float(a)
                p += log(pi)
        appen[t] = (-2.0) * p * (1.0 / (n_times - 2))
    return appen


@nb.jit([nb.float64[:](nb.float64[:, :]), nb.float32[:](nb.float32[:, :])],
        nopython=True)
def compute_samp_entropy(data):
    """ Sample Entropy (SampEn, per channel) [1].

    Parameters
    ----------
    data : shape (n_channels, n_times)

    Returns
    -------
    output : ndarray, shape (n_channels)

    References
    ----------
    .. [1] Teixeira, C. A. et al. (2011). EPILAB: A software package for
           studies on the prediction of epileptic seizures. Journal of
           Neuroscience Methods, 200(2), 257-271.
    """
    n_channels, n_times = data.shape
    sampen = np.empty((n_channels,), dtype=data.dtype)
    for t in range(n_channels):
        m = 0
        s = 0
        for j in range(n_times):
            m += data[t, j]
            s += data[t, j] ** 2
        m /= n_times
        s /= n_times
        s = sqrt(s)
        x_new = np.zeros(n_times)
        for j in range(n_times):
            x_new[j] = (data[t, j] - m) / s
        mm = 3
        r = 0.2
        lastrun = np.zeros((n_times,))
        run = np.zeros((n_times,))
        a = np.zeros((mm,))
        b = np.zeros((mm,))
        for i in range(n_times - 1):
            nj = n_times - i - 1
            y1 = x_new[i]
            for jj in range(nj):
                j = jj + i + 1
                if abs(x_new[j] - y1) < r:
                    run[jj] = lastrun[jj] + 1
                    m1 = int(min(mm, run[jj]))
                    for k in range(m1):
                        a[k] += 1
                        if j < (n_times - 1):
                            b[k] += 1
                else:
                    run[jj] = 0
            for jj in range(nj):
                lastrun[jj] = run[jj]
        sampen[t] = -log(a[-1] / b[mm - 2])
    return sampen


def compute_decorr_time(sfreq, data):
    """ Decorrelation time (per channel) [1].

    Parameters
    ----------
    sfreq : float
        Sampling rate of the data.

    data : ndarray, shape (n_channels, n_times)

    Returns
    -------
    output : ndarray, shape (n_channels,)

    References
    ----------
    .. [1] Teixeira, C. A. et al. (2011). EPILAB: A software package for
           studies on the prediction of epileptic seizures. Journal of
           Neuroscience Methods, 200(2), 257-271.
    """
    n_channels, n_times = data.shape
    decorrelation_times = np.empty((n_channels,))
    for j in range(n_channels):
        ac_channel = _unbiased_autocorr(data[j, :])
        zero_cross = ac_channel[(n_times - 1):] <= 0
        if np.any(zero_cross):
            decorr_time = np.argmax(zero_cross)
            decorr_time /= sfreq
        else:
            decorr_time = -1
        decorrelation_times[j] = decorr_time
    return decorrelation_times


def compute_power_spectrum_freq_bands(sfreq, data,
                                      freq_bands=np.array([0.5, 4., 8., 13.,
                                                           30., 100.]),
                                      normalize=True):
    """ Power Spectrum (computed by frequency bands) [1].

    Parameters
    ----------
    sfreq : float
        Sampling rate of the data.

    data : ndarray, shape (n_channels, n_times)

    freq_bands : ndarray, shape (n_freqs,)
        (default: np.array([0.5, 4., 8., 13., 30., 100.]))
        Array defining the frequency bands. The j-th frequency band is defined
        as: [freq_bands[j], freq_bands[j + 1]] (0 <= j <= n_freqs - 1).

    normalize : bool (default: True)
        If True, the average power in each frequency band is normalized by
        the total power.

    Returns
    -------
    output : ndarray, shape (n_channels * (n_freqs - 1),)

    References
    ----------
    .. [1] Teixeira, C. A. et al. (2011). EPILAB: A software package for
           studies on the prediction of epileptic seizures. Journal of
           Neuroscience Methods, 200(2), 257-271.
    """
    n_channels = data.shape[0]
    n_freqs = freq_bands.shape[0]
    ps, freqs = power_spectrum(sfreq, data, return_db=False)
    idx_freq_bands = np.digitize(freqs, freq_bands)
    pow_freq_bands = np.empty((n_channels, n_freqs - 1))
    for j in range(1, n_freqs):
        ps_band = ps[:, idx_freq_bands == j]
        pow_freq_bands[:, j - 1] = np.sum(ps_band, axis=-1)
    if normalize:
        pow_freq_bands = np.divide(pow_freq_bands,
                                   np.sum(ps, axis=-1)[:, None])
    return pow_freq_bands.ravel()


def compute_spect_hjorth_mobility(sfreq, data, normalize=False):
    """ Hjorth mobility (computed from the power spectrum, per channel) [1].

    Parameters
    ----------
    sfreq : float
        Sampling rate of the data.

    data : ndarray, shape (n_channels, n_times)

    normalize : bool (default: False)
        Normalize the result by the total power (see [2]).

    Returns
    -------
    output : ndarray, shape (n_channels,)

    References
    ----------
    .. [1] Mormann, F. et al. (2006). Seizure prediction: the long and winding
           road. Brain, 130(2), 314-333.

    .. [2] Teixeira, C. A. et al. (2011). EPILAB: A software package for
           studies on the prediction of epileptic seizures. Journal of
           Neuroscience Methods, 200(2), 257-271.
    """
    ps, freqs = power_spectrum(sfreq, data)
    w_freqs = np.power(freqs, 2)
    mobility = np.sum(np.multiply(ps, w_freqs), axis=-1)
    if normalize:
        mobility = np.divide(mobility, np.sum(ps, axis=-1))
    return mobility


def compute_spect_hjorth_complexity(sfreq, data, normalize=False):
    """ Hjorth complexity (computed from the power spectrum, per channel) [1].

    Parameters
    ----------
    sfreq : float
        Sampling rate of the data.

    data : ndarray, shape (n_channels, n_times)

    normalize : bool (default: False)
        Normalize the result by the total power (see [2]).

    Returns
    -------
    output : ndarray, shape (n_channels,)

    References
    ----------
    .. [1] Mormann, F. et al. (2006). Seizure prediction: the long and winding
           road. Brain, 130(2), 314-333.

    .. [2] Teixeira, C. A. et al. (2011). EPILAB: A software package for
           studies on the prediction of epileptic seizures. Journal of
           Neuroscience Methods, 200(2), 257-271.
    """
    ps, freqs = power_spectrum(sfreq, data)
    w_freqs = np.power(freqs, 4)
    complexity = np.sum(np.multiply(ps, w_freqs), axis=-1)
    if normalize:
        complexity = np.divide(complexity, np.sum(ps, axis=-1))
    return complexity


def compute_hjorth_mobility(data):
    """ Hjorth mobility (computed in the time domain, per channel) [1].

    Parameters
    ----------
    data : ndarray, shape (n_channels, n_times)

    Returns
    -------
    output : ndarray, shape (n_channels,)

    References
    ----------
    .. [1] Paivinen, N. et al. (2005). Epileptic seizure detection: A nonlinear
           viewpoint. Computer methods and programs in biomedicine, 79(2),
           151-159.
    """
    x = np.insert(data, 0, 0, axis=-1)
    dx = np.diff(x, axis=-1)
    sx = np.std(x, ddof=1, axis=-1)
    sdx = np.std(dx, ddof=1, axis=-1)
    mobility = np.divide(sdx, sx)
    return mobility


def compute_hjorth_complexity(data):
    """ Hjorth complexity (computed in the time domain, per channel) [1].

    Parameters
    ----------
    data : ndarray, shape (n_channels, n_times)

    Returns
    -------
    output : ndarray, shape (n_channels,)

    References
    ----------
    .. [1] Paivinen, N. et al. (2005). Epileptic seizure detection: A nonlinear
           viewpoint. Computer methods and programs in biomedicine, 79(2),
           151-159.
    """
    x = np.insert(data, 0, 0, axis=-1)
    dx = np.diff(x, axis=-1)
    m_dx = compute_hjorth_mobility(dx)
    m_x = compute_hjorth_mobility(data)
    complexity = np.divide(m_dx, m_x)
    return complexity


@nb.jit([nb.float64[:](nb.float64[:, :], nb.optional(nb.int64)),
         nb.float32[:](nb.float32[:, :], nb.optional(nb.int32))])
def compute_higuchi_fd(data, kmax=10):
    """ Higuchi Fractal Dimension (per channel) [1, 2].

    Parameters
    ----------
    data : ndarray, shape (n_channels, n_times)

    kmax : int (default: 10)
        Maximum delay/offset (in number of samples).

    Returns
    -------
    output : ndarray, shape (n_channels,)

    References
    ----------
    .. [1] Esteller, R. et al. (2001). A comparison of waveform fractal
           dimension algorithms. IEEE Transactions on Circuits and Systems I:
           Fundamental Theory and Applications, 48(2), 177-183.

    .. [2] Paivinen, N. et al. (2005). Epileptic seizure detection: A nonlinear
           viewpoint. Computer methods and programs in biomedicine, 79(2),
           151-159.
    """
    n_channels, n_times = data.shape
    higuchi = np.empty((n_channels,), dtype=data.dtype)
    for s in range(n_channels):
        lk = np.empty((kmax,))
        x_reg = np.empty((kmax,))
        y_reg = np.empty((kmax,))
        for k in range(1, kmax + 1):
            lm = np.empty((k,))
            for m in range(k):
                ll = 0
                n_max = floor((n_times - m - 1) / k)
                n_max = int(n_max)
                for j in range(1, n_max):
                    ll += abs(data[s, m + j * k] - data[s, m + (j - 1) * k])
                ll /= k
                ll *= (n_times - 1) / (k * n_max)
                lm[m] = ll
            # Mean of lm
            m_lm = 0
            for m in range(k):
                m_lm += lm[m]
            m_lm /= k
            lk[k - 1] = m_lm
            x_reg[k - 1] = log(1. / k)
            y_reg[k - 1] = log(m_lm)
        higuchi[s] = _slope_lstsq(x_reg, y_reg)
    return higuchi


def compute_katz_fd(data):
    """ Katz Fractal Dimension (per channel) [1].

    Parameters
    ----------
    data : ndarray, shape (n_channels, n_times)

    Returns
    -------
    output : ndarray, shape (n_channels,)

    References
    ----------
    .. [1] Esteller, R. et al. (2001). A comparison of waveform fractal
           dimension algorithms. IEEE Transactions on Circuits and Systems I:
           Fundamental Theory and Applications, 48(2), 177-183.
    """
    dists = np.abs(np.diff(data, axis=-1))
    ll = np.sum(dists, axis=-1)
    a = np.mean(dists, axis=-1)
    ln = np.log10(np.divide(ll, a))
    aux_d = data - data[:, 0, None]
    d = np.max(np.abs(aux_d[:, 1:]), axis=-1)
    katz = np.divide(ln, np.add(ln, np.log10(np.divide(d, ll))))
    return katz


def compute_zero_crossings(data):
    """ Number of zero crossings (per channel).

    Parameters
    ----------
    data : ndarray, shape (n_channels, n_times)

    Returns
    -------
    output : ndarray, shape (n_channels,)
    """
    return np.sum(np.diff(np.sign(data), axis=-1) != 0, axis=-1)


def compute_line_length(data):
    """ Line length (per channel) [1].

    Parameters
    ----------
    data : ndarray, shape (n_channels, n_times)

    Returns
    -------
    output : ndarray, shape (n_channels,)

    References
    ----------
    .. [1] Esteller, R. et al. (2001). Line length: an efficient feature for
           seizure onset detection. In Engineering in Medicine and Biology
           Society, 2001. Proceedings of the 23rd Annual International
           Conference of the IEEE (Vol. 2, pp. 1707-1710). IEEE.
    """
    return np.sum(np.abs(np.diff(data, axis=-1)), axis=-1)


def compute_spect_entropy(sfreq, data):
    """ Spectral Entropy (Shannon entropy of the power spectrum,
    per channel) [1].

    Parameters
    ----------
    sfreq : float
        Sampling rate of the data

    data : ndarray, shape (n_channels, n_times)

    Returns
    -------
    output : ndarray, shape (n_channels,)

    References
    ----------
    .. [1] Inouye, T. et al. (1991). Quantification of EEG irregularity by
           use of the entropy of the power spectrum. Electroencephalography
           and clinical neurophysiology, 79(3), 204-210.
    """
    ps, _ = power_spectrum(sfreq, data, return_db=False)
    m = np.sum(ps, axis=-1)
    ps_norm = np.divide(ps[:, 1:], m[:, None])
    return -np.sum(np.multiply(ps_norm, np.log2(ps_norm)), axis=-1)


def compute_svd_entropy(data, tau=2, emb=10):
    """ SVD entropy (per channel) [1].

    Parameters
    ----------
    data : ndarray, shape (n_channels, n_times)

    tau : int (default: 2)
        Delay (number of samples).

    emb : int (default: 10)
        Embedding dimension.

    Returns
    -------
    output : ndarray, shape (n_channels,)

    References
    ----------
    .. [1] Roberts, S. J. et al. Temporal and spatial complexity measures for
           electroencephalogram based brain-computer interfacing. Medical &
           biological engineering & computing, 37(1), 93-98.
    """
    _, sv, _ = np.linalg.svd(embed(data, d=emb, tau=tau))
    m = np.sum(sv, axis=-1)
    sv_norm = np.divide(sv, m[:, None])
    return -np.sum(np.multiply(sv_norm, np.log2(sv_norm)), axis=-1)


def compute_svd_fisher_info(data, tau=2, emb=10):
    """ SVD Fisher Information (per channel) [1].

    Parameters
    ----------
    data : ndarray, shape (n_channels, n_times)

    tau : int (default: 2)
        Delay (number of samples).

    emb : int (default: 10)
        Embedding dimension.

    Returns
    -------
    output : ndarray, shape (n_channels,)

    References
    ----------
    .. [1] Roberts, S. J. et al. Temporal and spatial complexity measures for
           electroencephalogram based brain-computer interfacing. Medical &
           biological engineering & computing, 37(1), 93-98.
    """
    _, sv, _ = np.linalg.svd(embed(data, d=emb, tau=tau))
    m = np.sum(sv, axis=-1)
    sv_norm = np.divide(sv, m[:, None])
    aux = np.divide(np.diff(sv_norm, axis=-1) ** 2, sv_norm[:, :-1])
    return np.sum(aux, axis=-1)


def compute_energy_freq_bands(sfreq, data, freq_bands=np.array([0.5, 4., 8.,
                                                                13., 30.,
                                                                100.]),
                              deriv_filt=True):
    """ Energy (of the signal, filtered by frequency bands ; per channel) [1].

    Parameters
    ----------
    sfreq : float
        Sampling rate of the data.

    data : ndarray, shape (n_channels, n_times)

    freq_bands : ndarray, shape (n_freqs,)
        (default: np.array([0.5, 4., 8., 13., 30., 100.]))
        Array defining the frequency bands. The j-th frequency band is defined
        as: [freq_bands[j], freq_bands[j + 1]] (0 <= j <= n_freqs - 1).

    deriv_filt : bool (default: False)
        If True, a derivative filter is applied to the input data before
        filtering (see Notes).

    Returns
    -------
    output : ndarray, shape (n_channels * (n_freqs - 1),)

    References
    ----------
    .. [1] Kharbouch, A. et al. (2011). An algorithm for seizure onset
           detection using intracranial EEG. Epilepsy & Behavior, 22, S29-S35.
    """
    n_freqs = freq_bands.shape[0]
    n_channels = data.shape[0]
    band_energy = np.empty((n_channels, n_freqs - 1))
    if deriv_filt:
        _data = convolve1d(data, [1., 0., -1.], axis=-1, mode='nearest')
    else:
        _data = data
    for j in range(1, n_freqs):
        filtered_data = filt(sfreq, _data, freq_bands[(j - 1):(j + 1)])
        band_energy[:, j - 1] = np.sum(filtered_data ** 2, axis=-1)
    return band_energy.ravel()


def compute_spect_edge_freq(sfreq, data, ref_freq=None, edge=None):
    """ Spectal Edge Frequency (per channel) [1].

    Parameters
    ----------
    sfreq : float
        Sampling rate of the data.

    data : ndarray, shape (n_channels, n_times)

    ref_freq : float or None (default: None)
        If not None, reference frequency for the computation of the spectral
        edge frequency. If None, `ref_freq = sfreq / 2` is used.

    edge : list of float or None (default: None)
        If not None, the values of `edge` are assumed to be positive and will
        be normalized to values between 0 and 1. Each entry of `edge`
        corresponds to a percentage. The spectral edge frequency will be
        computed for each different value in `edge`. If None, `edge = [0.5]`
        is used.

    Returns
    -------
    output : ndarray, shape (n_channels * n_edge,)
        With: `n_edge = 1` if `edge` is None or `n_edge = len(edge)` otherwise.

    References
    ----------
    .. [1] Mormann, F. et al. (2006). Seizure prediction: the long and winding
           road. Brain, 130(2), 314-333.
    """
    if ref_freq is None:
        _ref_freq = sfreq / 2
    else:
        _ref_freq = float(ref_freq)
    if edge is None:
        _edge = [0.5]
    else:
        _edge = [e / 100. for e in edge]
    n_edge = len(_edge)
    n_channels, n_times = data.shape
    spect_edge_freq = np.empty((n_channels, n_edge))
    ps, freqs = power_spectrum(sfreq, data, return_db=False)
    out = np.cumsum(ps, 1)
    for i, p in enumerate(_edge):
        idx_ref = np.where(freqs >= _ref_freq)[0][0]
        ref_pow = np.sum(ps[:, :(idx_ref + 1)], axis=-1)
        for j in range(n_channels):
            idx = np.where(out[j, :] >= p * ref_pow[j])[0]
            if idx.size > 0:
                spect_edge_freq[j, i] = freqs[idx[0]]
            else:
                spect_edge_freq[j, i] = -1
    return spect_edge_freq.ravel()


def compute_wavelet_coef_energy(data, wavelet_name='db4'):
    """ Energy of Wavelet decomposition coefficients (per channel) [1].

    Parameters
    ----------
    data : ndarray, shape (n_channels, n_times)

    wavelet_name : str (default: db4)
        Wavelet name (to be used with `pywt.Wavelet`). The full list of Wavelet
        names are given by: `[name for family in pywt.families() for name in
        pywt.wavelist(family)]`.

    Returns
    -------
    output : ndarray, shape (n_channels * levdec,)
        The decomposition level (`levdec`) used for the DWT is either 6 or
        the maximum useful decomposition level (given the number of time points
        in the data and chosen wavelet ; see `pywt.dwt_max_level`).

    References
    ----------
    .. [1] Teixeira, C. A. et al. (2011). EPILAB: A software package for
           studies on the prediction of epileptic seizures. Journal of
           Neuroscience Methods, 200(2), 257-271.
    """
    n_channels, n_times = data.shape
    wavelet = pywt.Wavelet(wavelet_name)
    levdec = min(pywt.dwt_max_level(n_times, wavelet.dec_len), 6)
    wavelet_energy = np.zeros((n_channels, levdec))
    for j in range(n_channels):
        coefs = pywt.wavedec(data[j, :], wavelet, level=levdec)
        for l in range(levdec):
            wavelet_energy[j, l] = np.sum(coefs[levdec - l] ** 2)
    return wavelet_energy.ravel()
