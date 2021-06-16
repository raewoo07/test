import multiprocessing
import sys
import itertools as it

import numpy as np
import mdtraj as md
from progressbar import ProgressBar

from scattering.utils.utils import get_dt
from scattering.utils.constants import get_form_factor


def compute_van_hove(trj, chunk_length, water=False,
                     r_range=(0, 1.0), bin_width=0.005, n_bins=None,
                     self_correlation=True, periodic=True, opt=True, partial=False):
    """Compute the partial van Hove function of a trajectory

    Parameters
    ----------
    trj : mdtraj.Trajectory
        trajectory on which to compute the Van Hove function
    chunk_length : int
        length of time between restarting averaging
    water : bool
        use X-ray form factors for water that account for polarization
    r_range : array-like, shape=(2,), optional, default=(0.0, 1.0)
        Minimum and maximum radii.
    bin_width : float, optional, default=0.005
        Width of the bins in nanometers.
    n_bins : int, optional, default=None
        The number of bins. If specified, this will override the `bin_width`
         parameter.
    self_correlation : bool, default=True
        Whether or not to include the self-self correlations

    Returns
    -------
    r : numpy.ndarray
        r positions generated by histogram binning
    g_r_t : numpy.ndarray
        Van Hove function at each time and position
    """

    n_physical_atoms = len([a for a in trj.top.atoms if a.element.mass > 0])
    unique_elements = list(set([a.element for a in trj.top.atoms if a.element.mass > 0]))

    data = []
    for elem1, elem2 in it.combinations_with_replacement(unique_elements[::-1], 2):
        data.append([
            trj,
            chunk_length,
            'element {}'.format(elem1.symbol),
            'element {}'.format(elem2.symbol),
            r_range,
            bin_width,
            n_bins,
            self_correlation,
            periodic,
            opt,
        ])

    manager = multiprocessing.Manager()
    partial_dict = manager.dict()
    jobs = []
    version_info = sys.version_info
    for d in data:
        with multiprocessing.Pool(processes=multiprocessing.cpu_count()) as pool:
            if version_info.major == 3 and version_info.minor <= 7:
                p = pool.Process(target=worker, args=(partial_dict, d))
            elif version_info.major == 3 and version_info.minor >= 8:
                ctx = multiprocessing.get_context()
                p = pool.Process(ctx, target=worker, args=(partial_dict, d))
            jobs.append(p)
            p.start()

    for proc in jobs:
            proc.join()

    r = partial_dict['r']
    del partial_dict['r']

    if partial:
        return partial_dict

    norm = 0
    g_r_t = None

    for key, val in partial_dict.items():
        elem1, elem2 = key
        concentration1 = trj.atom_slice(trj.top.select(elem1)).n_atoms / n_physical_atoms
        concentration2 = trj.atom_slice(trj.top.select(elem2)).n_atoms / n_physical_atoms
        form_factor1 = get_form_factor(element_name=elem1.split()[1], water=water)
        form_factor2 = get_form_factor(element_name=elem2.split()[1], water=water)

        coeff = form_factor1 * concentration1 * form_factor2 * concentration2
        if g_r_t is None:
            g_r_t = np.zeros_like(val)
        g_r_t += val * coeff

        norm += coeff

    # Reshape g_r_t to better represent the discretization in both r and t
    g_r_t_final = np.empty(shape=(chunk_length, len(r)))
    for i in range(chunk_length):
        g_r_t_final[i, :] = np.mean(g_r_t[i::chunk_length], axis=0)

    g_r_t_final /= norm

    t = trj.time[:chunk_length]

    return r, t, g_r_t_final


def worker(return_dict, data):
    key = (data[2], data[3])
    r, g_r_t_partial = compute_partial_van_hove(*data)
    return_dict[key] = g_r_t_partial
    return_dict['r'] = r


def compute_partial_van_hove(trj, chunk_length=10, selection1=None, selection2=None,
                             r_range=(0, 1.0), bin_width=0.005, n_bins=200,
                             self_correlation=True, periodic=True, opt=True):
    """Compute the partial van Hove function of a trajectory

    Parameters
    ----------
    trj : mdtraj.Trajectory
        trajectory on which to compute the Van Hove function
    chunk_length : int
        length of time between restarting averaging
    selection1 : str
        selection to be considered, in the style of MDTraj atom selection
    selection2 : str
        selection to be considered, in the style of MDTraj atom selection
    r_range : array-like, shape=(2,), optional, default=(0.0, 1.0)
        Minimum and maximum radii.
    bin_width : float, optional, default=0.005
        Width of the bins in nanometers.
    n_bins : int, optional, default=None
        The number of bins. If specified, this will override the `bin_width`
         parameter.
    self_correlation : bool, default=True
        Whether or not to include the self-self correlations

    Returns
    -------
    r : numpy.ndarray
        r positions generated by histogram binning
    g_r_t : numpy.ndarray
        Van Hove function at each time and position
    """

    unique_elements = (
        set([a.element for a in trj.atom_slice(trj.top.select(selection1)).top.atoms]),
        set([a.element for a in trj.atom_slice(trj.top.select(selection2)).top.atoms]),
    )

    if any([len(val) > 1 for val in unique_elements]):
        raise UserWarning(
            'Multiple elements found in a selection(s). Results may not be '
            'direcitly comprable to scattering experiments.'
        )

    # Don't need to store it, but this serves to check that dt is constant
    dt = get_dt(trj)

    pairs = trj.top.select_pairs(selection1=selection1, selection2=selection2)

    n_chunks = int(trj.n_frames / chunk_length)

    g_r_t = None
    pbar = ProgressBar()

    for i in pbar(range(n_chunks)):
        times = list()
        for j in range(chunk_length):
            times.append([chunk_length*i, chunk_length*i+j])
        r, g_r_t_frame = md.compute_rdf_t(
            traj=trj,
            pairs=pairs,
            times=times,
            r_range=r_range,
            bin_width=bin_width,
            n_bins=n_bins,
            period_length=chunk_length,
            self_correlation=self_correlation,
            periodic=periodic,
            opt=opt,
        )

        if g_r_t is None:
            g_r_t = np.zeros_like(g_r_t_frame)
        g_r_t += g_r_t_frame

    return r, g_r_t
