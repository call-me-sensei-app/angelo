// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT

#include "AngeloLinkGlb.h"

#include "DynamicMesh/DynamicMeshAttributeSet.h"
#include "GLTFAsset.h"
#include "GLTFReader.h"
#include "HAL/FileManager.h"

namespace AngeloLink
{
	namespace
	{
		constexpr double MetresToCentimetres = 100.0;   // Interchange's glTF unit conversion.

		/** Unreal's collision-mesh prefixes: Interchange makes these collision, not render geometry. */
		bool IsCollision(const FString& Name)
		{
			for (const TCHAR* Prefix : {TEXT("UCX_"), TEXT("UBX_"), TEXT("USP_"), TEXT("UCP_")})
			{
				if (Name.StartsWith(Prefix, ESearchCase::CaseSensitive))
				{
					return true;
				}
			}
			return false;
		}

		struct FBuilder
		{
			const GLTF::FAsset& Asset;
			FPreviewMesh& Out;
			TMap<int32, int32> SlotOfMaterial;   // glTF material index (INDEX_NONE: none) -> material slot.
			TSet<int32> Visited;                 // Nodes already baked (a malformed file may list a node twice).

			int32 Slot(int32 MaterialIndex)
			{
				if (const int32* Found = SlotOfMaterial.Find(MaterialIndex))
				{
					return *Found;
				}
				FLinearColor Color = FLinearColor::White;
				if (Asset.Materials.IsValidIndex(MaterialIndex))
				{
					const FVector4f& Factor = Asset.Materials[MaterialIndex].BaseColorFactor;
					Color = FLinearColor(Factor.X, Factor.Y, Factor.Z, Factor.W);
				}
				const int32 NewSlot = Out.SlotColors.Add(Color);
				SlotOfMaterial.Add(MaterialIndex, NewSlot);
				return NewSlot;
			}

			void AddPrimitive(const GLTF::FPrimitive& Primitive, const FMatrix& Global, const TArray<float>& Weights)
			{
				using namespace UE::Geometry;
				if (!Primitive.IsValid() || !Primitive.HasPositions())
				{
					return;
				}
				TArray<FVector3f> Positions;
				TArray<FVector3f> Normals;
				TArray<uint32> Indices;
				Primitive.GetPositions(Positions);
				Primitive.GetTriangleIndices(Indices);
				if (Primitive.HasNormals())
				{
					Primitive.GetNormals(Normals);
				}
				const bool bNormals = Normals.Num() == Positions.Num();
				// The mesh's current shape: its morph targets at their default weights, as Interchange imports it.
				TArray<FVector3f> Displacements;
				for (int32 Target = 0; Target < Primitive.MorphTargets.Num() && Target < Weights.Num(); ++Target)
				{
					const GLTF::FMorphTarget& Morph = Primitive.MorphTargets[Target];
					if (Weights[Target] == 0.0f || !Morph.HasPositionDisplacements())
					{
						continue;
					}
					Morph.GetPositionDisplacements(Displacements);
					for (int32 Index = 0; Index < Positions.Num() && Index < Displacements.Num(); ++Index)
					{
						Positions[Index] += Displacements[Index] * Weights[Target];
					}
					if (bNormals && Morph.HasNormalDisplacements())
					{
						Morph.GetNormalDisplacements(Displacements);
						for (int32 Index = 0; Index < Normals.Num() && Index < Displacements.Num(); ++Index)
						{
							Normals[Index] += Displacements[Index] * Weights[Target];
						}
					}
				}
				const FMatrix ForNormals = Global.Inverse().GetTransposed();
				const bool bMirrored = Global.Determinant() < 0.0;   // Reverse the winding, as Interchange does.

				FDynamicMesh3& Mesh = Out.Mesh;
				FDynamicMeshNormalOverlay* NormalOverlay = Mesh.Attributes()->PrimaryNormals();
				FDynamicMeshMaterialAttribute* Materials = Mesh.Attributes()->GetMaterialID();
				const int32 MaterialSlot = Slot(Primitive.MaterialIndex);
				TArray<int32> VertexOf;
				VertexOf.Init(INDEX_NONE, Positions.Num());
				auto Vertex = [&](uint32 Index) -> int32
				{
					if (VertexOf[Index] == INDEX_NONE)
					{
						const FVector Position = Global.TransformPosition(FVector(Positions[Index])) * MetresToCentimetres;
						VertexOf[Index] = Mesh.AppendVertex(Position);
					}
					return VertexOf[Index];
				};
				for (int32 Corner = 0; Corner + 2 < Indices.Num(); Corner += 3)
				{
					uint32 Corners[3] = {Indices[Corner], Indices[Corner + 1], Indices[Corner + 2]};
					if (bMirrored)
					{
						Swap(Corners[0], Corners[2]);
					}
					if (Corners[0] >= (uint32)Positions.Num() || Corners[1] >= (uint32)Positions.Num() ||
						Corners[2] >= (uint32)Positions.Num())
					{
						continue;   // FPrimitive::IsValid rejects these already; kept so a reader change can't overrun.
					}
					FIndex3i Triangle(Vertex(Corners[0]), Vertex(Corners[1]), Vertex(Corners[2]));
					int32 Added = Mesh.AppendTriangle(Triangle);
					if (Added == FDynamicMesh3::NonManifoldID || Added == FDynamicMesh3::DuplicateTriangleID)
					{   // Give the triangle its own vertices: Interchange keeps both faces of a two-sided sheet too.
						for (int32 Side = 0; Side < 3; ++Side)
						{
							Triangle[Side] = Mesh.AppendVertex(Mesh.GetVertex(Triangle[Side]));
						}
						Added = Mesh.AppendTriangle(Triangle);
					}
					if (Added < 0)
					{
						continue;   // Degenerate (a repeated corner).
					}
					const FVector3f Flat = (FVector3f)Mesh.GetTriNormal(Added);
					FIndex3i Elements;
					for (int32 Side = 0; Side < 3; ++Side)
					{
						FVector3f Normal = Flat;   // Missing normals are flat, as Interchange makes them.
						if (bNormals)
						{
							Normal = (FVector3f)FVector(ForNormals.TransformVector(FVector(Normals[Corners[Side]]))).GetSafeNormal();
						}
						Elements[Side] = NormalOverlay->AppendElement(Normal);
					}
					NormalOverlay->SetTriangle(Added, Elements);
					Materials->SetValue(Added, MaterialSlot);
				}
			}

			void AddNode(int32 NodeIndex, const FMatrix& Parent, int32 Depth)
			{
				if (!Asset.Nodes.IsValidIndex(NodeIndex) || Depth > 256 || Visited.Contains(NodeIndex))
				{
					return;
				}
				Visited.Add(NodeIndex);
				const GLTF::FNode& Node = Asset.Nodes[NodeIndex];
				const FMatrix Global = Node.Transform.ToMatrixWithScale() * Parent;
				if (Asset.Meshes.IsValidIndex(Node.MeshIndex) && !IsCollision(Node.Name))
				{
					const GLTF::FMesh& Mesh = Asset.Meshes[Node.MeshIndex];
					const TArray<float>& Weights = Node.MorphTargetWeights.Num() ? Node.MorphTargetWeights
																				 : Mesh.MorphTargetWeights;
					// A skinned mesh's vertices are in its skeleton's space: glTF ignores the node's own transform.
					const FMatrix& Placed = Node.Type == GLTF::FNode::EType::MeshSkinned ? FMatrix::Identity : Global;
					for (const GLTF::FPrimitive& Primitive : Mesh.Primitives)
					{
						AddPrimitive(Primitive, Placed, Weights);
					}
				}
				for (const int32 Child : Node.Children)
				{
					AddNode(Child, Global, Depth + 1);
				}
			}
		};
	}

	bool ReadGlb(const FString& GlbPath, FPreviewMesh& Out, FString& Error)
	{
		if (IFileManager::Get().FileSize(*GlbPath) <= 0)
		{
			Error = FString::Printf(TEXT("Angelo's live scene isn't there (%s)"), *FPaths::GetCleanFilename(GlbPath));
			return false;
		}
		GLTF::FAsset Asset;
		GLTF::FFileReader Reader;
		Reader.ReadFile(GlbPath, /*bInLoadImageData=*/false, /*bInLoadMetadata=*/false, Asset);
		for (const GLTF::FLogMessage& Message : Reader.GetLogMessages())
		{
			if (Message.Get<0>() == GLTF::EMessageSeverity::Error)
			{
				Error = FString::Printf(TEXT("Couldn't read Angelo's live scene: %s"), *Message.Get<1>().ToString());
				return false;
			}
		}
		Out.Mesh.Clear();
		Out.Mesh.EnableAttributes();
		Out.Mesh.Attributes()->EnableMaterialID();
		Out.SlotColors.Reset();
		FBuilder Builder{Asset, Out};
		TArray<int32> Roots;   // Every scene's, as Interchange imports every scene; else the root nodes.
		for (const GLTF::FScene& Scene : Asset.Scenes)
		{
			Roots.Append(Scene.Nodes);
		}
		if (Roots.Num() == 0)
		{
			Asset.GetRootNodes(Roots);
		}
		for (const int32 Root : Roots)
		{
			Builder.AddNode(Root, FMatrix::Identity, 0);
		}
		return true;
	}
}
