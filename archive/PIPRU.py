#!/usr/bin/env python3
import subprocess
import re
from pathlib import Path
import json
import urllib.request
import urllib.error
import shutil
from datetime import datetime
import logging
import venv
import sys
import argparse
import pkg_resources
import hashlib
from typing import Dict, Tuple, Optional, List
from packaging import version
from packaging.specifiers import SpecifierSet

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('requirements_updates.log', encoding='utf-8')
    ]
)

class RequirementsManager:
    def __init__(self, requirements_path: str = 'requirements.txt', target_python: Optional[str] = None):
        self.requirements_path = Path(requirements_path)
        self.backup_dir = Path('requirements_backups')
        self.backup_dir.mkdir(exist_ok=True)
        self.current_python = self.get_python_version()
        self.target_python = version.parse(target_python) if target_python else self.current_python
        
    def get_python_version(self) -> version.Version:
        """Get current Python version."""
        return version.parse('.'.join(map(str, sys.version_info[:3])))
        
    def create_backup(self) -> Path:
        """Create a backup of the current requirements file with content hash."""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # Generate content hash
        with open(self.requirements_path, 'rb') as f:
            content = f.read()
            content_hash = hashlib.md5(content).hexdigest()[:8]  # Using first 8 chars for brevity
            
        backup_path = self.backup_dir / f'requirements_{timestamp}_{content_hash}.txt'
        shutil.copy2(self.requirements_path, backup_path)
        logging.info(f"Created backup at {backup_path}")
        return backup_path

    def get_installed_version(self, package_name: str) -> Optional[str]:
        """Get the version of a package installed in the current environment."""
        try:
            return pkg_resources.get_distribution(package_name).version
        except pkg_resources.DistributionNotFound:
            try:
                # Try using pip if pkg_resources fails
                result = subprocess.run(
                    [sys.executable, '-m', 'pip', 'show', package_name],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    for line in result.stdout.splitlines():
                        if line.startswith('Version:'):
                            return line.split(':', 1)[1].strip()
            except Exception:
                pass
            return None

    def parse_requirement(self, req: str) -> Tuple[str, Optional[str], Optional[str]]:
        """Parse a requirement line into (package_name, operator, version)."""
        req = req.strip()
        if not req or req.startswith('#'):
            return req, None, None
            
        # Match package name and version constraint
        pattern = r'^([a-zA-Z0-9\-._]+)(?:(>=|<=|>|<|==|~=)(.+))?$'
        match = re.match(pattern, req)
        
        if not match:
            return req, None, None
            
        package_name, operator, version = match.groups()
        return package_name, operator, version

    def is_zero_version(self, ver_str: str) -> bool:
        """Check if version ends with .0"""
        return ver_str.endswith('.0')

    def is_compatible_version(self, requires_python: Optional[str], version_str: str) -> bool:
        """Check if a package version is compatible with target Python version."""
        if not requires_python:
            return True
        try:
            python_spec = SpecifierSet(requires_python)
            return python_spec.contains(str(self.target_python))
        except Exception:
            return True

    def get_stable_version(self, package_name: str) -> Tuple[Optional[str], Optional[str]]:
        """Get both latest and stable (one behind) versions of a package from PyPI.
        
        Returns:
            Tuple of (stable_version, latest_version)
        """
        try:
            url = f"https://pypi.org/pypi/{package_name}/json"
            with urllib.request.urlopen(url) as response:
                data = json.loads(response.read())
                releases = data["releases"]
                
                # Filter and sort versions
                valid_versions = []
                for ver, release_info in releases.items():
                    try:
                        parsed_ver = version.parse(ver)
                        # Skip pre-releases and empty releases
                        if not parsed_ver.is_prerelease and release_info:
                            # Get Python version requirement
                            requires_python = None
                            if release_info and isinstance(release_info, list):
                                requires_python = release_info[0].get('requires_python')
                            
                            # Check Python version compatibility and .0 versions
                            if self.is_compatible_version(requires_python, ver) and not self.is_zero_version(ver):
                                valid_versions.append((parsed_ver, requires_python))
                    except version.InvalidVersion:
                        continue
                
                if not valid_versions:
                    return None, None
                
                # Sort versions in descending order
                sorted_versions = sorted(valid_versions, key=lambda x: x[0], reverse=True)
                
                # Get latest version
                latest_ver = str(sorted_versions[0][0])
                latest_req = sorted_versions[0][1]
                if latest_req:
                    logging.info(f"Latest version {latest_ver} requires Python {latest_req}")
                
                # Get one version behind latest if available
                stable_ver = str(sorted_versions[1][0]) if len(sorted_versions) > 1 else latest_ver
                stable_req = sorted_versions[1][1] if len(sorted_versions) > 1 else latest_req
                if stable_req:
                    logging.info(f"Stable version {stable_ver} requires Python {stable_req}")
                
                return stable_ver, latest_ver
                
        except (urllib.error.URLError, json.JSONDecodeError, KeyError) as e:
            logging.error(f"Error fetching version for {package_name}: {str(e)}")
            return None, None

    def get_test_env_name(self, packages_to_update: Optional[List[str]] = None) -> str:
        """Generate test environment name based on packages being updated."""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        python_ver = f"py{self.target_python}"
        if packages_to_update:
            # Sort packages for consistent naming
            packages_str = '-'.join(sorted(packages_to_update))
            return f"test_env_{packages_str}_{python_ver}_{timestamp}"
        return f"test_env_all_{python_ver}_{timestamp}"

    def cleanup_test_env(self, test_env_dir: Path):
        """Clean up the test virtual environment."""
        if test_env_dir.exists():
            try:
                shutil.rmtree(test_env_dir)
            except Exception as e:
                logging.warning(f"Failed to clean up test environment: {str(e)}")

    def test_requirements(self, requirements_path: Path, packages_to_update: Optional[List[str]] = None) -> bool:
        """Test requirements in a virtual environment."""
        # Create test environment with package names in folder
        test_env_name = self.get_test_env_name(packages_to_update)
        test_env_dir = Path(test_env_name)
        
        # Clean up any existing test environment with same name
        self.cleanup_test_env(test_env_dir)

        # If target Python is different from current, warn and skip testing
        if self.target_python != self.current_python:
            logging.warning(f"""
Skipping test environment creation as target Python {self.target_python} differs from current {self.current_python}.
Requirements have been updated for Python {self.target_python}.
Please test the requirements in an environment with Python {self.target_python} using:
1. Create a new environment with Python {self.target_python}
2. Activate the environment
3. Run: pip install -r {requirements_path}
""")
            return True
        
        try:
            # Create virtual environment
            logging.info(f"Creating test environment: {test_env_name}")
            venv.create(test_env_dir, with_pip=True)
            
            # Determine paths
            python_path = test_env_dir / 'Scripts' / 'python.exe' if sys.platform == 'win32' else test_env_dir / 'bin' / 'python'
            
            # Upgrade pip first using python -m pip
            subprocess.run(
                [str(python_path), '-m', 'pip', 'install', '--upgrade', 'pip'],
                check=True,
                capture_output=True,
                text=True
            )
            
            # Install requirements
            result = subprocess.run(
                [str(python_path), '-m', 'pip', 'install', '-r', str(requirements_path)],
                capture_output=True,
                text=True
            )
            
            if result.returncode != 0:
                logging.error(f"Requirements test failed: {result.stderr}")
                return False
            
            logging.info(f"""
Requirements test successful!
Test environment '{test_env_name}' is ready for application testing.
- To activate: {test_env_dir}/Scripts/activate (Windows) or source {test_env_dir}/bin/activate (Unix)
- To remove when done: simply delete the {test_env_name} directory
""")
            return True
            
        except Exception as e:
            logging.error(f"Error testing requirements: {str(e)}")
            return False

    def list_packages(self) -> List[Tuple[str, Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]]:
        """List all packages with their current versions and available versions."""
        if not self.requirements_path.exists():
            logging.error(f"Requirements file not found: {self.requirements_path}")
            return []

        packages = []
        with open(self.requirements_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                package_name, operator, current_version = self.parse_requirement(line)
                installed_version = self.get_installed_version(package_name)
                stable_version, latest_version = self.get_stable_version(package_name)
                packages.append((package_name, operator, current_version, installed_version, stable_version, latest_version))
        
        return packages

    def update_requirements(self, packages_to_update: Optional[List[str]] = None, test: bool = True) -> bool:
        """Update requirements.txt with stable package versions."""
        if not self.requirements_path.exists():
            logging.error(f"Requirements file not found: {self.requirements_path}")
            return False

        if self.target_python != self.current_python:
            logging.info(f"Updating requirements for Python {self.target_python} (current: {self.current_python})")
            if test:
                logging.info("Note: Testing will be skipped as target Python version differs from current")

        # Create backup
        backup_path = self.create_backup()
        
        # Read current requirements
        with open(self.requirements_path, 'r') as f:
            current_requirements = f.readlines()
        
        updated_requirements = []
        updates_made = False
        logging.info(f"Checking for updates (Python {self.target_python})...")
        
        for req in current_requirements:
            req = req.strip()
            if not req or req.startswith('#'):
                updated_requirements.append(req)
                continue
                
            package_name, operator, current_version = self.parse_requirement(req)
            
            # Skip if not in packages_to_update
            if packages_to_update is not None and package_name not in packages_to_update:
                # Ensure version constraint exists even for skipped packages
                if not operator or not current_version:
                    installed_version = self.get_installed_version(package_name)
                    if installed_version:
                        updated_requirements.append(f"{package_name}=={installed_version}")
                        updates_made = True
                        logging.info(f"[ADD VERSION] {package_name}: added installed version =={installed_version}")
                    else:
                        stable_version, _ = self.get_stable_version(package_name)
                        if stable_version:
                            updated_requirements.append(f"{package_name}=={stable_version}")
                            updates_made = True
                            logging.info(f"[ADD VERSION] {package_name}: added stable version =={stable_version}")
                        else:
                            updated_requirements.append(req)
                else:
                    updated_requirements.append(req)
                continue
            
            # Get installed and available versions
            logging.info(f"Checking {package_name}...")
            installed_version = self.get_installed_version(package_name)
            stable_version, latest_version = self.get_stable_version(package_name)
            
            if not operator or not current_version:
                # No version specified, use installed version if available
                if installed_version:
                    updated_requirements.append(f"{package_name}=={installed_version}")
                    updates_made = True
                    logging.info(f"[ADD VERSION] {package_name}: added installed version =={installed_version}")
                elif stable_version:
                    updated_requirements.append(f"{package_name}=={stable_version}")
                    updates_made = True
                    logging.info(f"[ADD VERSION] {package_name}: added stable version =={stable_version}")
                else:
                    updated_requirements.append(req)
                    logging.warning(f"[WARN] Could not determine version for {package_name}, keeping as is")
            elif stable_version:
                if operator in ('>', '<', '>=', '<=', '~='):
                    # Keep existing constraint
                    updated_requirements.append(f"{package_name}{operator}{current_version}")
                    logging.info(f"[OK] {package_name}: keeping constraint {operator}{current_version}")
                else:
                    # Update to stable version
                    updated_requirements.append(f"{package_name}=={stable_version}")
                    if current_version != stable_version:
                        updates_made = True
                        logging.info(f"[UPDATE] {package_name}: {current_version} -> {stable_version}")
                        if latest_version and latest_version != stable_version:
                            logging.info(f"[INFO] Latest version {latest_version} available but using {stable_version} for stability")
                    else:
                        logging.info(f"[OK] {package_name}: already at stable version {stable_version}")
            else:
                updated_requirements.append(req)
                logging.warning(f"[WARN] Could not find stable version for {package_name}, keeping current version")
        
        # Write updated requirements
        with open(self.requirements_path, 'w') as f:
            f.write('\n'.join(updated_requirements) + '\n')
        
        if updates_made and test:
            logging.info("Testing updated requirements...")
            if not self.test_requirements(self.requirements_path, packages_to_update):
                # Restore backup if test fails
                logging.warning("Test failed! Restoring previous requirements...")
                shutil.copy2(backup_path, self.requirements_path)
                return False
            
            logging.info("All updates have been tested and applied successfully!")
        elif updates_made:
            logging.info(f"Requirements have been updated for Python {self.target_python}")
        else:
            logging.info("No updates were necessary or available.")
        
        return True

def main():
    parser = argparse.ArgumentParser(description='Python Requirements Manager')
    parser.add_argument('--list', action='store_true', help='List all packages with their versions')
    parser.add_argument('--update', nargs='*', metavar='PACKAGE',
                       help='Update specific packages (space-separated) or all if no packages specified')
    parser.add_argument('--no-test', action='store_true', help='Skip testing updated requirements')
    parser.add_argument('--file', default='requirements.txt', help='Path to requirements file')
    parser.add_argument('--python', help='Target Python version (e.g., 3.8, 3.9, 3.10)')
    
    args = parser.parse_args()
    manager = RequirementsManager(args.file, args.python)
    
    if args.list:
        packages = manager.list_packages()
        current_py = manager.current_python
        target_py = manager.target_python
        py_status = " (target)" if target_py != current_py else ""
        print(f"\nCurrent packages (Python {current_py} → {target_py}{py_status}):")
        print("-" * 120)
        print(f"{'Package':<20} {'Required':<15} {'Installed':<15} {'Stable':<15} {'Latest':<15} {'Status'}")
        print("-" * 120)
        for name, operator, current, installed, stable, latest in packages:
            if operator in ('>', '<', '>=', '<=', '~='):
                status = f"Constraint: {operator}{current}"
                version_display = f"{operator}{current}"
            else:
                if not current:
                    if installed:
                        status = f"Using installed {installed}"
                        version_display = "None"
                    else:
                        status = "No version specified"
                        version_display = "None"
                elif stable:
                    if current == stable:
                        status = "Up to date"
                        version_display = current
                    else:
                        status = "Update available"
                        version_display = current
                else:
                    status = "Unknown"
                    version_display = current
            print(f"{name:<20} {version_display:<15} {installed or 'N/A':<15} {stable or 'N/A':<15} {latest or 'N/A':<15} {status}")
        print("-" * 120)
        if any(not op and not curr for _, op, curr, _, _, _ in packages):
            print("\nWarning: Some packages don't have version constraints. Use --update to add them.")
        if target_py != current_py:
            print(f"\nNote: Showing versions compatible with Python {target_py}")
            print("Testing will be skipped as target Python version differs from current")
    elif args.update is not None:  # None means argument wasn't provided, [] means no packages specified
        packages = args.update if args.update else None  # None means update all
        success = manager.update_requirements(packages, not args.no_test)
        if not success:
            sys.exit(1)
    else:
        parser.print_help()

if __name__ == '__main__':
    main()