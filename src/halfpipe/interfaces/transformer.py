# -*- coding: utf-8 -*-
# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:

from pathlib import Path

import nibabel as nib
import numpy as np
import pandas as pd
from nilearn.image import new_img_like
from nipype.interfaces.base import File, SimpleInterface, TraitedSpec, isdefined, traits
from numpy import typing as npt

from ..ingest.spreadsheet import read_spreadsheet
from ..utils.image import nvol
from ..utils.path import split_ext


class TransformerInputSpec(TraitedSpec):
    in_file = File(desc="File to filter", exists=True, mandatory=True)
    mask = File(desc="mask to use for volumes", exists=True)

    write_header = traits.Bool(default_value=True, usedefault=True)


class TransformerOutputSpec(TraitedSpec):
    out_file = File()


class Transformer(SimpleInterface):
    """
    Interface that takes any kind of array input and applies a function to it
    """

    input_spec = TransformerInputSpec
    output_spec = TransformerOutputSpec

    suffix = "transformed"

    def _transform(self, array: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
        raise NotImplementedError

    def _load(self, in_file, mask_file=None):
        stem, ext = split_ext(in_file)
        self.stem, self.ext = stem, ext

        if mask_file is None:
            mask_file = self.inputs.mask

        self.mask = None

        if ext in [".nii", ".nii.gz"]:
            in_img = nib.nifti1.load(in_file)
            self.in_img = in_img

            ndim = np.asanyarray(in_img.dataobj).ndim
            if ndim == 3:
                volumes = [in_img]
            elif ndim == 4:
                volumes = nib.funcs.four_to_three(in_img)
            else:
                raise ValueError(f'Unexpect number of dimensions {ndim:d} in "{in_file}"')

            volume_shape = volumes[0].shape
            n_voxels = np.prod(volume_shape)

            if isdefined(mask_file) and isinstance(mask_file, str) and Path(mask_file).is_file():
                mask_img = nib.funcs.squeeze_image(nib.nifti1.load(mask_file))

                if nvol(mask_img) != 1:
                    raise ValueError(f'Expecting a single volume for mask file "{mask_file}"')
                if in_img.affine is not None:
                    if not np.allclose(mask_img.affine, in_img.affine):
                        raise ValueError(f'Affine mismatch between "{in_file}" and "{mask_file}"')

                mask_fdata = mask_img.get_fdata(dtype=np.float64)
                mask_bin = np.logical_not(np.logical_or(mask_fdata <= 0, np.isclose(mask_fdata, 0, atol=1e-2)))

                self.mask = mask_bin
                n_voxels = np.count_nonzero(mask_bin)

            n_volumes = len(volumes)

            array = np.zeros((n_volumes, n_voxels))

            for i, volume in enumerate(volumes):
                volume_data = volume.get_fdata()

                if self.mask is not None:
                    array[i, :] = volume_data[self.mask]
                else:
                    array[i, :] = np.ravel(volume_data)

        else:  # a text file
            in_df = read_spreadsheet(in_file)
            self.in_df = in_df

            array = in_df.to_numpy().astype(np.float64)

        return array

    def _dump(self, array2):
        stem, ext = self.stem, self.ext

        out_file = str(Path(f"{stem}_{self.suffix}{ext}").resolve())

        if ext in [".nii", ".nii.gz"]:
            in_img = self.in_img

            if self.mask is not None:
                _, n = array2.T.shape
                out_array = np.zeros((*in_img.shape[:3], n))
                out_array[self.mask, :] = array2.T
            else:
                out_array = array2.T.reshape((*in_img.shape[:3], -1))

            # squeeze time axis if we are outputting a single volume
            if out_array.shape[3] == 1:
                out_array = np.squeeze(out_array, axis=3)

            out_img = new_img_like(in_img, out_array, copy_header=True)
            assert isinstance(out_img.header, nib.nifti1.Nifti1Header)

            out_img.header.set_data_dtype(np.float64)
            nib.loadsave.save(out_img, out_file)

        else:
            header = True
            if (
                hasattr(self.inputs, "write_header")
                and isdefined(self.inputs.write_header)
                and self.inputs.write_header is False
            ):
                header = False

            out_df = pd.DataFrame(data=array2, columns=self.in_df.columns)
            out_df.to_csv(
                out_file,
                sep="\t",
                index=False,
                na_rep="n/a",
                header=header,
            )

        return out_file

    def _run_interface(self, runtime):
        self._merged_file = None

        in_file = self.inputs.in_file

        array = self._load(in_file)  # observations x variables

        array2 = self._transform(array)

        out_file = self._dump(array2)
        self._results["out_file"] = out_file

        return runtime
