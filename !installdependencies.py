import subprocess

# List of packages to install
packages = ['glfw', 'PyOpenGL', 'PyOpenGL_accelerate', 'Pillow', 'tk' ,'py-spy']

# Iterate through the packages and install them using pip
for package in packages:
    subprocess.check_call(['pip', 'install', package])
