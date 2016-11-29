clean:
	-rm -rf doc/build build dist MANIFEST novajoin.egg-info
	-find . -name '*.py[oc]' -exec rm {} \;
.PHONY: clean

sdist: clean
	python setup.py sdist --formats=gztar
.PHONY: sdist
