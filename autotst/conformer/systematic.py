#!/usr/bin/python
# -*- coding: utf-8 -*-

##########################################################################
#
#   AutoTST - Automated Transition State Theory
#
#   Copyright (c) 2015-2018 Prof. Richard H. West (r.west@northeastern.edu)
#
#   Permission is hereby granted, free of charge, to any person obtaining a
#   copy of this software and associated documentation files (the 'Software'),
#   to deal in the Software without restriction, including without limitation
#   the rights to use, copy, modify, merge, publish, distribute, sublicense,
#   and/or sell copies of the Software, and to permit persons to whom the
#   Software is furnished to do so, subject to the following conditions:
#
#   The above copyright notice and this permission notice shall be included in
#   all copies or substantial portions of the Software.
#
#   THE SOFTWARE IS PROVIDED 'AS IS', WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#   IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#   FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#   LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
#   FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
#   DEALINGS IN THE SOFTWARE.
#
##########################################################################
import itertools
import logging
import pandas as pd
import numpy as np
import os
from multiprocessing import Process, Manager

import ase
from ase import Atoms
from ase import calculators
from ase.optimize import BFGS

import autotst
from autotst.species import Conformer
from autotst.reaction import TS
from autotst.conformer.utilities import get_energy, find_terminal_torsions


def find_all_combos(
        conformer,
        delta=float(60),
        cistrans=True,
        chiral_centers=True):
    """
    A function to find all possible conformer combinations for a given conformer
    """

    terminal_torsions, torsions = find_terminal_torsions(conformer)
    cistranss = conformer.cistrans
    chiral_centers = conformer.chiral_centers

    torsion_angles = np.arange(0, 360, delta)
    torsion_combos = list(itertools.combinations_with_replacement(
        torsion_angles, len(torsions)))
    if len(torsions) != 1:
        torsion_combos = list(
            set(
                torsion_combos +
                list(itertools.combinations_with_replacement(
                    torsion_angles[::-1], len(torsions)
                ))))

    if cistrans:
        cistrans_options = ["E", "Z"]
        cistrans_combos = list(itertools.combinations_with_replacement(
            cistrans_options, len(cistranss)))
        if len(cistranss) != 1:
            cistrans_combos = list(
                set(
                    cistrans_combos +
                    list(itertools.combinations_with_replacement(
                        cistrans_options[::-1], len(cistranss)
                    ))))

    else:
        cistrans_combos = [()]

    if chiral_centers:
        chiral_options = ["R", "S"]
        chiral_combos = list(itertools.combinations_with_replacement(
            chiral_options, len(chiral_centers)))
        if len(chiral_centers) != 1:
            chiral_combos = list(
                set(
                    chiral_combos +
                    list(itertools.combinations_with_replacement(
                        chiral_options[::-1], len(chiral_centers)
                    ))))
    else:
        chiral_combos = [()]

    all_combos = list(
        itertools.product(
            torsion_combos,
            cistrans_combos,
            chiral_combos))
    return all_combos


def systematic_search(conformer,
                      delta=float(30),
                      cistrans=True,
                      chiral_centers=True,
                      store_results=False,
                      store_directory="."):
    """
    Perfoms a brute force conformer analysis of a molecule or a transition state

    :param autotst_object: am autotst_ts, autotst_rxn, or autotst_molecule that you want to perform conformer analysis on
       * the ase_object of the autotst_object must have a calculator attached to it.
    :param store_generations: do you want to store pickle files of each generation
    :param store_directory: the director where you want the pickle files stored
    :param delta: the degree change in dihedral angle between each possible dihedral angle

    :return results: a DataFrame containing the final generation
    :return unique_conformers: a dictionary with indicies of unique torsion combinations and entries of energy of those torsions
    """
    # Takes each of the molecule objects

    combos = find_all_combos(
        conformer,
        delta=delta,
        cistrans=cistrans,
        chiral_centers=chiral_centers)

    if not np.any(combos):
        logging.info("This species has no torsions, cistrans bonds, or chiral centers")
        logging.info("Returning origional conformer")
        return [conformer]

    terminal_torsions, torsions = find_terminal_torsions(conformer)
    file_name = conformer.smiles + "_brute_force.csv"

    calc = conformer.ase_molecule.get_calculator()

    results = []
    conformers = {}
    combinations = {}
    for index, combo in enumerate(combos):

        combinations[index] = combo

        torsions, cistrans, chiral_centers = combo

        for i, torsion in enumerate(torsions):

            tor = conformer.torsions[i]
            i, j, k, l = tor.atom_indices
            mask = tor.mask

            conformer.ase_molecule.set_dihedral(
                a1=i,
                a2=j,
                a3=k,
                a4=l,
                angle=torsion,
                mask=mask
            )
            conformer.update_coords()

        for i, e_z in enumerate(cistrans):
            ct = conformer.cistrans[i]
            conformer.set_cistrans(ct.index, e_z)

        for i, s_r in enumerate(chiral_centers):
            center = conformer.chiral_centers[i]
            conformer.set_chirality(center.atom_index, s_r)

        conformer.update_coords_from("ase")

        conformers[index] = conformer.copy()

    logging.info("There are {} unique conformers generated".format(len(conformers)))

    final_results = []

    def opt_conf(conformer, calculator, i):

        combo = combinations[i]
        if isinstance(conformer, TS):
            atoms = conformer.rmg_molecule.getLabeledAtoms()
            labels = []
            labels.append([atoms["*1"].sortingLabel, atoms["*2"].sortingLabel])
            labels.append([atoms["*3"].sortingLabel, atoms["*2"].sortingLabel])
            #labels.append([atoms["*1"].sortingLabel, atoms["*3"].sortingLabel])
            from ase.constraints import FixBondLengths
            c = FixBondLengths(labels)
            conformer.ase_molecule.set_constraint(c)
            label = conformer.reaction_label

        else:
            label = conformer.smiles

        conformer.ase_molecule.set_calculator(calculator)
        
        opt = BFGS(conformer.ase_molecule, logfile=None)
        #try:
        opt.run()
        conformer.update_coords_from("ase")
        energy = get_energy(conformer)

        return_dict[i] = (energy, conformer.ase_molecule.arrays, conformer.ase_molecule.get_all_distances())

    manager = Manager()
    return_dict = manager.dict()

    processes = []
    for i, conf in conformers.items():
        p = Process(target=opt_conf, args=(conf,calc,i))
        p.start()
        processes.append(p)
    complete = np.zeros_like(processes, dtype=bool)
    while not np.all(complete):
        for i, p in enumerate(processes):
            if not p.is_alive():
                complete[i] = True

    from ase import units
    results = []
    for key, values in return_dict.items():
        results.append(values)
        
    df = pd.DataFrame(results, columns=["energy", "arrays", 'distances'])
    df = df[df.energy < df.energy.min() + units.kcal / units.mol / units.eV].sort_values("energy")
    scratch_index = []
    unique_index = []
    for index, distances in zip(df.index, df.distances):
        if index in scratch_index:
            continue

            
        unique_index.append(index)
        is_close = (np.sqrt(((df['distances'][index] - df.distances)**2).apply(np.mean)) > 0.1)
        scratch_index += [d for d in is_close[is_close == False].index if not (d in scratch_index)]
        
    logging.info("We have identified {} unique conformers for {}".format(len(unique_index), conformer))
    confs = []
    for i, info in enumerate(df[["energy", "arrays"]].loc[unique_index].values):
        copy_conf = conformer.copy()
        
        energy, array = info
        copy_conf.energy = energy
        copy_conf.index = i
        copy_conf.ase_molecule.set_positions(array["positions"])
        copy_conf.update_coords_from("ase")
        confs.append(copy_conf.copy())
        
    return confs
