var rm = new TF.Backend.RequestManager();
var backend = new TF.Backend.Backend("/components/tf-tensorboard/demo/giant_data", rm, true);

function makeHistogramDashboard(el: HTMLElement, elScope: any) {

  var chartsContainer: d3.Selection<HCategory> = d3.select(el);

  var data = [];

  //
  // Chart sizing
  //
  var numColumns = 2 * 2 * 2; //must be power of two
  var chartAspectRatio = 0.75;
  var stageWidth;
  var chartWidth;
  var chartHeight;

  //
  // Radar
  //
  var frame = elScope.$.frame;
  var radar = new TF.NodeRadar(frame);
  var scrollContainer = document.querySelector("#mainContainer");
  var radarResponse;
  var visibleCharts;
  var almostVisibleCharts;
  var allCharts = [];
  var chartsByRunTag = {}; //dictionary to find chart nodes from noderadar values.

  // Scan every so many milliseconds to keep us honest. Could be better.
  setInterval(scan, 500);
  function scan() {
    render();
    console.log("Scanning");
    // radarResponse = radar.scan();
    // var getChart = function(n:any) { return { chart: chartsByRunTag[n.run + n.tag], run: n.run, tag: n.tag }; };
    // var hiddenCharts = radarResponse.hidden.map(getChart);
    // visibleCharts = radarResponse.visible.map(getChart);
    // almostVisibleCharts = visibleCharts.concat(radarResponse.almost.map(getChart));
    // allCharts = almostVisibleCharts.concat(hiddenCharts);
    // updateChartSize();
    // almostVisibleCharts.forEach(function(d) {
    //   if (!d.chart.dataRequested) {
    //     console.log("Requesting");
    //     backend.histograms(d.run, d.tag).then(function(data) {
    //       mutateChart(d.chart, "data", processData(data));
    //     });
    //     d.chart.dataRequested = true;
    //   }
    //   if (d.chart.dirty) {
    //     drawChart(d.chart)
    //   }
    // });
  }

  //
  // Events from actions panel
  //
  var actionsPanel = elScope.$.actions;

  actionsPanel.addEventListener("zoomchange", function(e) {
    numColumns = (e.detail.value === "in" ? numColumns / 2 : numColumns * 2);
    render();
  });

  actionsPanel.addEventListener("modechange", function(e) {
    allCharts.forEach(function(chart) {
      mutateChart(chart, "mode", e.detail.value);
    });
    updateVisibleCharts(1000);
  });

  actionsPanel.addEventListener("timechange", function(e) {
    allCharts.forEach(function(chart) {
      mutateChart(chart, "time", e.detail.value);
    });
    updateVisibleCharts(1000);
  });

  function mutateChart(c, property, value) {
    c[property] = value;
    c.dirty = true;
  }

  function drawChart(c, animationDuration?: number) {
    c.draw(animationDuration)
    c.dirty = false
  }

  function updateVisibleCharts(animationDuration) {
    visibleCharts.each(function(d:any) {
      console.log("draw")
      drawChart(this, animationDuration);
    });
  }


  //
  // Render skeleton HTML
  //

  interface RunTag {
    run: string;
    tag: string;
  }

  interface HCategory {
    name: string;
    runTags: RunTag[];
  }

  function histogramCategories(x: TF.Backend.RunsResponse): HCategory[] {
    var enumerations = <TF.Backend.RunEnumeration[]> _.values(x);
    var tags: string[][] = enumerations.map((e) => e.histograms);
    var all_tags: string[] = _.union.apply(null, tags);
    var categorizer = Categorizer.topLevelNamespaceCategorizer;
    var categories = categorizer(all_tags);

    var runNames = _.keys(x);
    function tag2runtag(t: string): RunTag[] {
      return runNames.filter((r) => {
        return x[r].histograms.indexOf(t) !== -1;
      }).map((r) => {return {tag: t, run: r}});
    }

    return categories.map((c) => {
      return {
        name: c.name,
        runTags: _.flatten(c.tags.map(tag2runtag))
      };
    });
  };

  backend.runs().then((x) => {
    data = histogramCategories(x);
    data.forEach(function(d: any) {
      d.runsByTag = d3.nest()
          .key(function(d: any) { return d.tag; })
          .entries(d.runTags);
    });
    // updateChartSize();
    render();

    // This adds the css scoping necessary for the new elements
    elScope.scopeSubtree(elScope.$.content, true);
  });

  function layout() {
    console.time("layout");
    var categoryMargin = {top: 60, bottom: 20};
    var tagMargin = {top: 35, bottom: 30};
    var chartMargin = {top: 15, right: 10};

    stageWidth = el.getBoundingClientRect().width - 48;
    chartWidth = Math.floor(stageWidth / numColumns) - chartMargin.right;
    chartHeight = Math.min(
      frame.getBoundingClientRect().height - 40,
      Math.floor(chartWidth * chartAspectRatio) - chartMargin.top
    );

    var cumulativeCategoryHeight = 0;
    data.forEach(function(category, ci) {
      category.y = cumulativeCategoryHeight;
      var cumulativeTagHeight = 0;
      category.runsByTag.forEach(function(tag, ti) {
        tag.height = chartHeight * Math.ceil(tag.values.length / numColumns) + tagMargin.bottom + tagMargin.top;
        tag.y = cumulativeTagHeight + categoryMargin.top;
        tag.pageY = category.y + tag.y;
        tag.values.forEach(function(run, ri) {
          run.x = (ri % numColumns) * (chartWidth + chartMargin.right);
          run.y = Math.floor(ri / numColumns) * (chartHeight + chartMargin.top) + tagMargin.top;
        });
        cumulativeTagHeight += tag.height;
      });
      category.height = cumulativeTagHeight + categoryMargin.bottom + categoryMargin.top;
      cumulativeCategoryHeight += category.height;
    });
    console.timeEnd("layout");
  }


  function render() {
    console.time("render");
    layout();

    var scrollContainerHeight = scrollContainer.getBoundingClientRect().height;
    var scrollContainerTop = scrollContainer.scrollTop;
    var scrollContainerBottom = scrollContainer.scrollTop + scrollContainerHeight;
    var bufferTop = scrollContainerTop - scrollContainerHeight;
    var bufferBottom = scrollContainerBottom + scrollContainerHeight;

    var category = chartsContainer.selectAll(".category").data(data, (d: any) => d.name),
        categoryExit = category.exit().remove(),
        categoryEnter = category.enter().append("div").attr("class", "category"),
        categoryUpdate = category
            .style("top", (d) => d.y + "px")
            .style("height", (d) => d.height + "px");

    // Filter to just visible categories.
    categoryUpdate = categoryUpdate.filter(function(d) {
      return d.y < bufferBottom && (d.y + d.height) >= bufferTop;
    });

    categoryEnter.append("h3")
        .text((d) => d.name);

    var tag = categoryUpdate.selectAll(".tag").data((d: any) => d.runsByTag, (d: any) => d.key),
        tagExit = tag.exit().remove(),
        tagEnter = tag.enter().append("div").attr("class", "tag"),
        tagUpdate = tag
            .style("top", (d) => d.y + "px")
            .style("height", (d) => d.height + "px");

    // Filter to just visible tags.
    tagUpdate = tagUpdate.filter(function(d) {
      return d.pageY < bufferBottom && (d.pageY + d.height) >= bufferTop;
    });

    tagEnter.append("h4")
        .text((d) => d.key);

    var run = tagUpdate.selectAll(".run").data((d: any) => d.values, (d: any) => d.run),
        runExit = run.exit().remove(),
        runEnter = run.enter().append("div").attr("class", "run"),
        runUpdate = run
            .style("left", (d) => d.x + "px")
            .style("top", (d) => d.y + "px")
            .style("width", chartWidth + "px")
            .style("height", chartHeight + "px");

    runEnter.append("h5")
        .text((d: any) => d.run);

    var histogramEnter = runEnter.append("tf-vz-histogram-series")
            .style("top", "15px"),
        histogramUpdate = runUpdate.select("tf-vz-histogram-series");

    histogramEnter.each(function(d) {
      allCharts.push(this);
    });

    histogramUpdate.each(function(d) {
      var chart = this;
      if (!chart.dataRequested) {
        backend.histograms(d.run, d.tag).then(function(data) {
          mutateChart(chart, "data", processData(data));
        });
        chart.dataRequested = true;
      }
      if (chart.width !== chartWidth || chart.height !== chartHeight - 15) {
        mutateChart(chart, "width", chartWidth);
        mutateChart(chart, "height", chartHeight - 15);
      }
      if (chart.dirty) {
        console.log("drawing chart");
        drawChart(chart);
      }
    });

    visibleCharts = histogramUpdate;

    console.timeEnd("render");
  }

  // if (!d.chart.dataRequested) {
  //     console.log("Requesting");
  //     backend.histograms(d.run, d.tag).then(function(data) {
  //       mutateChart(d.chart, "data", processData(data));
  //     });
  //     d.chart.dataRequested = true;
  //   }
  //   if (d.chart.dirty) {
  //     drawChart(d.chart)
  //   }

  //
  //TODO Processing Data. This needs some work.
  //
  function processData(data: any) {
    data.forEach(function(dd: any, i: Number) {
      var prev = null;
      dd.wallDate = new Date(dd.wall_time);
      dd.wall = dd.wallDate ? dd.wallDate.valueOf() : null;
      dd.i = i;
      dd.histogramData = dd.bucketRightEdges.map(function(ddd: any, i) {
        var bin: any = {};
        var value = (ddd === 0 ? -1e-12 : ddd)
        if (prev === null) {
          if (value > 0) {
            bin.left = (value / 1.1);
          } else {
            bin.left = (value * 1.1);
          }
        } else {
          bin.left = prev;
        }
        if (value > dd.max) {
          if (value > 0) {
            bin.right = bin.left * 1.1;
          } else {
            bin.right = bin.left / 1.1;
          }
        } else {
          bin.right = value;
        }
        bin.center = (bin.left + bin.right) / 2;
        bin.count = dd.bucketCounts[i];
        bin.area =  bin.count / (bin.right - bin.left);
        prev = ddd;
        return bin;
      });

      // TODO rebin and remove this...
      dd.histogramData = dd.histogramData.filter(function(d) { return (d.right - d.left) > 0.0035; })

      dd.binExtent = [dd.min, dd.max];
      dd.countExtent = d3.extent(dd.histogramData, function(d:any) { return d.count; });
      dd.areaMax = d3.max(dd.histogramData, function(d:any) { return d.area; })
      dd.leftMin = d3.min(dd.histogramData, function(d:any) { return d.left; });
      dd.rightMax = d3.max(dd.histogramData, function(d:any) { return d.right; });
    });
    return data.filter(function(d) { return d.step; }); //TODO Bad, some step values are undefined
  }
}