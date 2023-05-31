import csv
import numpy as np
import json

class DataLoader:
    def getAncestriesList(self):
        data = []
        with open('data/summary/uniq_broader.txt') as file:
            data = file.read().splitlines()
        ancestries = {}
        for ancestry in data:
            ancestries[ancestry] = ancestry.lower().replace(" ", "-").replace("/", "-")
        return ancestries
    def getAncestriesListOrder(self):
        data = []
        with open('data/summary/uniq_broader.txt') as file:
            data = file.read().splitlines()
        ancestries = {}
        i = 1
        for ancestry in data:
            ancestries[i] = ancestry
            i = i + 1
        return ancestries
    def getTermsList(self):
        data = []
        with open('data/summary/uniq_parent.txt') as file:
            data = file.read().splitlines()
        terms = {}
        for term in data:
            terms[term] = term.lower().replace(" ", "-")
        return terms
    def getTraitsList(self):
        data = []
        with open('data/summary/uniq_dis_trait.txt') as file:
            data = file.read().splitlines()
        traits = {}
        for trait in data:
            traits[trait] = trait.lower().replace(" ", "-").replace("(", "").replace(")", "")
        return traits
    def getSummaryStatistics(self):
        summary = {}
        with open('data/summary/summary.json') as json_file:
            data = json.load(json_file)
            for value in data:
                summary[value] = data[value]

        return summary

    def getBubbleGraph(self):
        dataInitial = {}
        dataReplication = {}

        with open('data/toplot/bubble_df.csv') as csv_file:
            csv_reader = csv.reader(csv_file, delimiter=',')

            line_count = 0
            j = 0
            for row in csv_reader:
                if line_count == 0:
                    keys = row
                    #keys.remove("") this removes empty index column, which is no longer present
                else:
                    if(row[5] == "replication"):
                        dataReplication[j] = {}
                        i = 0
                        for value in row[0:]:
                            dataReplication[j][keys[i]] = value
                            i += 1
                    elif(row[5] == "initial"):
                        dataInitial[j] = {}
                        i = 0
                        for value in row[0:]:
                            dataInitial[j][keys[i]] = value
                            i += 1
                line_count += 1
                j += 1
        return {
            'bubblegraph_initial': dataInitial,
            'bubblegraph_replication': dataReplication,
        }

    def getDoughnutGraph(self, ancestryOrder):
        dataDiscoveryStudies = {}
        dataDiscoveryParticipants = {}
        dataReplicationStudies = {}
        dataReplicationParticipants = {}
        dataAssociations = {}
        with open('data/toplot/doughnut_df.csv') as csv_file:
            csv_reader = csv.reader(csv_file, delimiter=',')
            line_count = 0
            for row in csv_reader:
                if line_count > 0:
                    year = row[2]
                    if year not in dataDiscoveryStudies:
                        dataDiscoveryStudies[year] = dict()
                        dataDiscoveryParticipants[year] = dict()
                        dataReplicationStudies[year] = dict()
                        dataReplicationParticipants[year] = dict()
                        dataAssociations[year] = dict()
                    term = row[1]
                    if term not in dataDiscoveryStudies[year]:
                        dataDiscoveryStudies[year][term] = dict()
                        dataDiscoveryParticipants[year][term] = dict()
                        dataReplicationStudies[year][term] = dict()
                        dataReplicationParticipants[year][term] = dict()
                        dataAssociations[year][term] = dict()
                    ancestry = row[0]
                    ancestryKey = list(ancestryOrder.keys())[list(ancestryOrder.values()).index(ancestry)]
                    dataDiscoveryStudies[year][term][ancestryKey] = {
                        "ancestry": row[0],
                        "value": row[5],
                        "funder": row[3]
                    }
                    dataDiscoveryParticipants[year][term][ancestryKey] = {
                        "ancestry": row[0],
                        "value": row[4],
                        "funder": row[3]
                    }
                    dataReplicationStudies[year][term][ancestryKey] = {
                        "ancestry": row[0],
                        "value": row[7],
                        "funder": row[3]
                    }
                    dataReplicationParticipants[year][term][ancestryKey] = {
                        "ancestry": row[0],
                        "value": row[6],
                        "funder": row[3]
                    }
                    dataAssociations[year][term][ancestryKey] = {
                        "ancestry": row[0],
                        "value": row[8],
                        "funder": row[3]
                    }
                line_count += 1
        return {
            'doughnut_discovery_studies': dataDiscoveryStudies,
            'doughnut_discovery_participants': dataDiscoveryParticipants,
            'doughnut_replication_studies': dataReplicationStudies,
            'doughnut_replication_participants': dataReplicationParticipants,
            'doughnut_associations': dataAssociations
        }
    def getHeatMap(self):
        return {
            'heatmap_discovery_studies' : self.getHeatMapData("heatmap_count_initial.csv"),
            'heatmap_replication_studies' : self.getHeatMapData("heatmap_count_replication.csv"),
            'heatmap_replication_participants' : self.getHeatMapData("heatmap_sum_replication.csv"),
            'heatmap_discovery_participants' : self.getHeatMapData("heatmap_sum_initial.csv"),
        }
    def getHeatMapData(self, filename):
        data = {}

        with open('data/toplot/'+filename) as csv_file:
            csv_reader = csv.reader(csv_file, delimiter=',')

            line_count = 0
            i = 0
            year = 0
            for row in csv_reader:
                if line_count == 0:
                    keys = row
                    keys.remove("")
                    keys.remove("Year")
                    keys.remove("Funder")
                else:
                    if int(float(row[len(row) - 2])) != int(float(year)):
                        year = int(float(row[len(row) - 2]))
#                        i = 0
                    if year not in data:
                        data[year] = dict()

                    j = 0
                    for value in row[1:len(row) - 2]:
                        data[year][i] = {
                            "ancestry": row[0],
                            "term": keys[j],
                            "value": np.round(float(value), 2),
                            "funder": row[len(row) - 1]
                        }
                        j += 1
                        i += 1
                line_count += 1
        return data

    def getChloroMap(self):
        data = {}

        with open('data/toplot/choro_df.csv') as csv_file:
            csv_reader = csv.reader(csv_file, delimiter=',')

            line_count = 0
            i = 0
            year = 0
            for row in csv_reader:
                if line_count > 0:

                    if row[7] != year:
                        year = row[7]
                        i = 0

                    if year not in data:
                        data[year] = dict()

                    data[year][i] = {
                        'country' : row[8],
                        'population' : row[0],
                        'studies' : row[2],
                        'studiesPercentage' : row[3],
                        'participants' : row[5],
                        'participantsPercentage' : row[6],
                        'funder': row[4]
                    }
                    i += 1
                line_count += 1

        return data
    def getTSPlot(self):
        return {
            'ts_notrecorded_discovery_studies' : self.getTSPlotData("ts1_initial_count.csv"),
            'ts_notrecorded_discovery_participants' : self.getTSPlotData("ts1_initial_sum.csv"),
            'ts_notrecorded_replication_studies' : self.getTSPlotData("ts1_replication_count.csv"),
            'ts_notrecorded_replication_participants' : self.getTSPlotData("ts1_replication_sum.csv"),
            'ts_recorded_discovery_studies' : self.getTSPlotData("ts2_initial_count.csv"),
            'ts_recorded_discovery_participants' : self.getTSPlotData("ts2_initial_sum.csv"),
            'ts_recorded_replication_studies' : self.getTSPlotData("ts2_replication_count.csv"),
            'ts_recorded_replication_participants' : self.getTSPlotData("ts2_replication_sum.csv"),
        }

    def getTSPlotData(self, filename):
        tsPlot = dict()
        with open('data/toplot/'+filename) as csv_file:
            csv_reader = csv.reader(csv_file, delimiter=',')
            line_count = 0
            for row in csv_reader:
                if line_count == 0:
                    keys = row
                    keys.remove("Year")
                    keys.remove("Funder")
                    for key in keys:
                        tsPlot[key] = dict()
                else:
                    year = row[0]
                    i = 1;
                    for key in keys:
                        tsPlot[key][line_count-1] = {
                            'year' : year,
                            'value' : row[i],
                            'funder': row[-1]
                        }
                        i += 1
                line_count += 1
        return tsPlot

    def filterTraits(self, search_trait):
        traits = self.getTraitsList()
        filtered_traits = []
        for count, (trait_key, trait_value) in enumerate(traits.items()):
            if search_trait in trait_key or search_trait in trait_value:
                filtered_traits.append({"id": count, "text": trait_key})
        return filtered_traits
