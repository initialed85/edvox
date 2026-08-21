#!/bin/bash

set -e

input_file="${1}"

if [[ "${input_file}" == "" ]]; then
	echo "error: first arg must be file to convert"
	exit 1
fi

echo "input_file: ${input_file}"

base="${input_file%%.*}"

temp_file="${base}-temp.wav"

output_file="${base}.wav"

echo "output_file: ${output_file}"

ffmpeg -i "${input_file}" -vn "${temp_file}"

ffmpeg -i "${temp_file}" -map_metadata -1 -map_chapters -1 -fflags +bitexact -flags:v +bitexact -flags:a +bitexact -c copy "${output_file}"

rm -f "${temp_file}"
